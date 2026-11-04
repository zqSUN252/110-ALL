"""Bounded external responses; callers choose fixed allowlisted provider endpoints."""
import httpx


async def bounded_request(client,method,url,max_bytes=1024*1024,**kwargs):
    async with client.stream(method,url,**kwargs) as response:
        content=bytearray()
        async for chunk in response.aiter_bytes():
            if len(content)+len(chunk)>max_bytes:raise ValueError('response_limit')
            content.extend(chunk)
        headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-encoding','content-length')}
        return httpx.Response(response.status_code,headers=headers,content=bytes(content),request=response.request)
