import os
from google import genai
from dotenv import load_dotenv

# 1. Load your .env file
load_dotenv()

# 2. Initialize the client
# It will look for the GEMINI_API_KEY environment variable automatically
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

try:
    # 3. Make a simple request
    response = client.models.generate_content(
        model=model,
        contents="Say 'The Gemini API is working!' if you can hear me."
    )
    
    # 4. Print the result
    print("--- Success! ---")
    print(f"Model: {model}")
    print(f"Response: {response.text}")

except Exception as e:
    print("--- Failure ---")
    print(f"Error details: {e}")