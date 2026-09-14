import os
from dotenv import load_dotenv
load_dotenv()
print(repr(os.getenv("MOENV_API_KEY")))