import aiohttp
import async_timeout


async def fetch(session, url):
    try:
        with async_timeout.timeout(15):
            async with session.get(
                url, headers={"Accept": "application/json"}
            ) as response:
                return await response.text()
    except:
        pass


async def request(url, self):
    async with aiohttp.ClientSession() as session:
        return await fetch(session, url)
