from fastapi import APIRouter
from api.routes import company_routes, product_routes, chat_routes, upload_routes

router = APIRouter()

router.include_router(company_routes.router, tags=["Empresas"])
router.include_router(product_routes.router, tags=["Produtos"])
router.include_router(chat_routes.router, tags=["I.A. & Chat"])
router.include_router(upload_routes.router, tags=["Uploads"])