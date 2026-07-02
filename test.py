import asyncio
import httpx
import os
from dotenv import load_dotenv

load_dotenv()

SCRAPECREATORS_API_KEY = os.getenv("SCRAPECREATORS_API_KEY")

async def test():
    async with httpx.AsyncClient() as http:
        response = await http.get(
            "https://api.scrapecreators.com/v1/instagram/profile",
            params={"handle": "zuck"},
            headers={"x-api-key": SCRAPECREATORS_API_KEY},
        )
        print(response.json())

asyncio.run(test())