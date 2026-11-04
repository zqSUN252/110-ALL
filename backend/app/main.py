from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.settings import load_settings, protect_private_root
from app.db import make_engine, session_factory
from app.common import DomainError
from app.api.identity import router as identity_router
from app.api.files import router as file_router
from app.api.finance import router as finance_router
from app.api.logistics import router as logistics_router


def create_app(settings=None):
    settings = settings or load_settings()
    protect_private_root(settings.private_root)
    application = FastAPI(title='MC STAR LOGISTICS', docs_url=None, redoc_url=None)
    application.state.settings = settings
    application.state.engine = make_engine(settings.database_url)
    application.state.session_factory = session_factory(application.state.engine)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # FastAPI's default includes rejected inputs, including passwords.
        return JSONResponse({'error': {'code': 'validation_error', 'message': 'Invalid request',
            'fields': [{'location': list(error['loc']), 'type': error['type']} for error in exc.errors()]}}, status_code=422)

    @application.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse({'error': {'code': exc.code, 'message': exc.message, 'details': exc.details}}, status_code=exc.status)

    @application.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse({'error': {'code': 'conflict', 'message': 'Record conflicts with existing data'}}, status_code=409)

    @application.get('/health')
    def health():
        with application.state.engine.connect() as db:
            db.execute(text('SELECT 1'))
        return {'status': 'ok'}

    application.include_router(identity_router)
    application.include_router(file_router)
    application.include_router(finance_router)
    application.include_router(logistics_router)
    return application
