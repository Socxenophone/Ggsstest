import socketio
import eventlet
from flask import Flask
import requests
import os
import logging
from logging.handlers import RotatingFileHandler # For logging to a file with rotation

# --- Configuration ---
# Load configuration from environment variables.
# These should be set in your production environment.
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY') # MANDATORY: OpenAI API Key
SERVER_HOST = os.environ.get('SERVER_HOST', '0.0.0.0') # Host to bind to (0.0.0.0 for all interfaces)
SERVER_PORT = int(os.environ.get('SERVER_PORT', 5000)) # Port to listen on
OPENAI_API_URL = os.environ.get('OPENAI_API_URL', "https://api.openai.com/v1/chat/completions")
# Production: Restrict this to your frontend's actual origin(s)!
# Example: ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', 'http://localhost:3000').split(',')
ALLOWED_ORIGINS = os.environ.get('ALLOWED_ORIGINS', '*').split(',') # '*' is DANGEROUS in production!

# --- Logging Setup ---
# Configure logging more comprehensively for production
LOG_FILE = os.environ.get('LOG_FILE', 'chat_backend.log') # Log file name
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper() # Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

# Create logger
logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# Create handlers
# Console handler for development/debugging
console_handler = logging.StreamHandler()
console_handler.setLevel(LOG_LEVEL)

# File handler for production logging
# Rotates logs after 1MB, keeps 5 backup files
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=1024*1024, backupCount=5)
file_handler.setLevel(LOG_LEVEL)

# Create formatters and add them to handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add handlers to the logger
# Avoid adding console handler in production unless necessary
logger.addHandler(console_handler)
logger.addHandler(file_handler)

# --- Flask and SocketIO Setup ---
app = Flask(__name__)
# Create a Socket.IO server instance
# Use the configured allowed origins
sio = socketio.Server(cors_allowed_origins=ALLOWED_ORIGINS, logger=True, engineio_logger=True) # Enable SocketIO/EngineIO logging
app.wsgi_app = socketio.WSGIApp(sio, app.wsgi_app)

# --- Canned Responses ---
# A dictionary mapping specific user inputs (lowercase) to predefined responses.
canned_responses = {
    "hello": "Greetings. How may I be of service?",
    "how are you": "As well as can be expected, given the circumstances. And you?",
    "what is the time": "One moment, I shall consult the chronometer.",
    "tell me a secret": "A good butler keeps his secrets, sir.",
    "what do you know about spain": "Spain, a land of rich history and vibrant culture. A fascinating place indeed.",
    "goodnight": "Sleep well, master. May your rest be undisturbed.",
    "thank you": "You are most welcome, sir."
}

# --- SocketIO Event Handlers ---

@sio.event
def connect(sid, environ):
    """Handles new client connections."""
    logger.info(f"Client connected: {sid}")
    # Optional: Send a welcome message upon connection
    # try:
    #     sio.emit('message', {'user': 'Butler', 'text': 'Welcome, master.'}, room=sid)
    # except Exception as e:
    #     logger.error(f"Error sending welcome message to {sid}: {e}")


@sio.event
def disconnect(sid):
    """Handles client disconnections."""
    logger.info(f"Client disconnected: {sid}")

@sio.event
def message(sid, data):
    """
    Handles incoming messages from clients.

    Args:
        sid (str): The session ID of the client.
        data (dict): The message data, expected to contain a 'text' key.
    """
    logger.info(f"Message from {sid}: {data}")
    user_message = data.get('text', '').strip()

    if not user_message:
        logger.warning(f"Received empty message from {sid}")
        return # Ignore empty messages

    # --- Process Message ---
    response_text = None

    # 1. Check for canned responses (case-insensitive)
    if user_message.lower() in canned_responses:
        response_text = canned_responses[user_message.lower()]
        logger.info(f"Using canned response for '{user_message}': {response_text}")
    else:
        # 2. If no canned response, send to OpenAI API
        logger.info(f"Sending message to OpenAI API: '{user_message}'")

        # Ensure API key is set before attempting to call OpenAI
        if not OPENAI_API_KEY:
            response_text = "Master, the external service key is not configured."
            logger.critical("OPENAI_API_KEY is not set. Cannot call OpenAI API.") # Critical error
        else:
            try:
                headers = {
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": "gpt-3.5-turbo", # Or a more cost-effective/powerful model as needed
                    "messages": [
                        {"role": "system", "content": "You are a professional butler assisting your master. Respond concisely and politely."}, # Further refined system message
                        {"role": "user", "content": user_message}
                    ],
                    "temperature": 0.7, # Control randomness
                    "max_tokens": 150 # Limit response length to control cost/verbosity
                }

                # Use a timeout for the API request
                openai_response = requests.post(OPENAI_API_URL, headers=headers, json=payload, timeout=20) # Increased timeout slightly
                openai_response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)

                response_data = openai_response.json()

                if response_data and 'choices' in response_data and response_data['choices']:
                    response_text = response_data['choices'][0]['message']['content'].strip()
                    logger.info(f"Received response from OpenAI: {response_text}")
                else:
                    response_text = "Forgive me, master, the external service returned an uninterpretable response."
                    logger.error(f"OpenAI API returned unexpected response format: {response_data}")

            except requests.exceptions.Timeout:
                response_text = "Master, the external service took too long to respond. Pray, try again."
                logger.error("OpenAI API request timed out.")
            except requests.exceptions.RequestException as e:
                response_text = f"I encountered an issue contacting the external service, master: {e}"
                logger.error(f"Error calling OpenAI API: {e}")
            except Exception as e:
                response_text = "An unexpected internal error has occurred, master."
                logger.exception("An unexpected error occurred during OpenAI processing:") # Log exception traceback


    # --- Send Response Back to Client ---
    if response_text:
        try:
            # Emit the response back to the client who sent the message
            sio.emit('message', {'user': 'Butler', 'text': response_text}, room=sid)
            logger.info(f"Sent response to {sid}: {response_text}")
        except Exception as e:
            logger.error(f"Error sending message to client {sid}: {e}")


# --- Running the Server ---
if __name__ == '__main__':
    if not OPENAI_API_KEY:
        logger.critical("OPENAI_API_KEY environment variable is not set. The server will run, but OpenAI calls will fail.")
        print("\n!!! CRITICAL WARNING: OPENAI_API_KEY is not set. OpenAI calls will not work. Set this environment variable for production. !!!\n")

    logger.info(f"Starting SocketIO server on http://{SERVER_HOST}:{SERVER_PORT}")
    print(f"Starting SocketIO server on http://{SERVER_HOST}:{SERVER_PORT}")

    # --- Production Deployment Recommendation ---
    # For production, it is highly recommended to use a production-ready WSGI server
    # like Gunicorn or uWSGI with eventlet workers.
    # Example command using Gunicorn:
    # gunicorn -k eventlet -w 4 server:app -b 0.0.0.0:5000 --log-level info
    # (Replace 'server' with the name of your Python file)
    # Running eventlet.wsgi.server directly is suitable for development/testing.
    try:
        eventlet.wsgi.server(eventlet.listen((SERVER_HOST, SERVER_PORT)), app)
    except Exception as e:
        logger.critical(f"Failed to start server: {e}", exc_info=True) # Log exception traceback
        print(f"Error: Failed to start server: {e}")

