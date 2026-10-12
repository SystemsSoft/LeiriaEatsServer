# Arquivo: conectaai/api/routes/creator_routes.py
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from conectaai.core.database import get_db
from conectaai.core.security import CurrentUser, require_role
from conectaai.core.uploads import save_avatar_image
from conectaai.repositories.company_repo import CompanyRepository
from conectaai.repositories.creator_repo import CreatorRepository
from conectaai.repositories.proposal_repo import ProposalRepository
from conectaai.repositories.rating_repo import RatingRepository
from conectaai.schemas.creator import (
    CreatorResponse,
    CreatorUpdateRequest,
    InstagramConnectRequest,
    creator_to_response as _to_response,
)
from conectaai.schemas.rating import RatingRequest, RatingResponse
from conectaai.services import instagram_service

router = APIRouter(prefix="/creators", tags=["Creators"])


@router.get("", response_model=List[CreatorResponse])
def list_creators(db: Session = Depends(get_db)):
    return [_to_response(c) for c in CreatorRepository.get_all(db)]


@router.get("/me", response_model=CreatorResponse)
def get_my_profile(
    current_user: CurrentUser = Depends(require_role("creator")), db: Session = Depends(get_db)
):
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")
    return _to_response(creator)


@router.put("/me", response_model=CreatorResponse)
def update_my_profile(
    data: CreatorUpdateRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")

    update_data = data.dict(exclude_unset=True)
    if "audience" in update_data:
        update_data["audience_info"] = update_data.pop("audience")
    creator = CreatorRepository.update(db, creator, update_data)
    return _to_response(creator)


@router.post("/me/avatar", response_model=CreatorResponse)
async def upload_my_avatar(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")

    url = await save_avatar_image(file)
    creator = CreatorRepository.update(db, creator, {"avatar_url": url})
    return _to_response(creator)


def _with_instagram_link(platforms, username: str) -> list:
    """`platforms` com o link do Instagram trocado pelo do perfil confirmado (mantendo a posição), ou
    acrescentado se o creator ainda não tinha o Instagram na lista. Sem `username` (desconectar), o Instagram
    continua na lista mas sem link — e não é acrescentado se não estava lá."""
    link = {"platform": "Instagram", "url": f"https://www.instagram.com/{username}" if username else ""}
    result, replaced = [], False
    for item in platforms or []:
        name = item if isinstance(item, str) else (item or {}).get("platform", "")
        if name == "Instagram":
            if not replaced:
                result.append(link)
                replaced = True
        else:
            result.append(item)
    return result if replaced or not username else result + [link]


@router.post("/me/instagram", response_model=CreatorResponse)
def connect_my_instagram(
    data: InstagramConnectRequest,
    current_user: CurrentUser = Depends(require_role("creator")),
    db: Session = Depends(get_db),
):
    """Conclui o "Conectar Instagram": troca o `code` do login do Instagram pelo perfil da conta que autorizou e
    grava esse Instagram como confirmado no perfil do creator. Uma conta do Instagram confirma um perfil só."""
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")

    try:
        profile = instagram_service.fetch_profile(data.code, data.redirect_uri)
    except instagram_service.InstagramUnavailable:
        raise HTTPException(status_code=503, detail="A conexão com o Instagram ainda não está disponível.")
    except instagram_service.InstagramError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    owner = CreatorRepository.get_by_instagram_user_id(db, profile.user_id)
    if owner and owner.id != creator.id:
        raise HTTPException(status_code=409, detail="Esse Instagram já está conectado a outro perfil do ConectaAI.")

    creator = CreatorRepository.update(db, creator, {
        "instagram_user_id": profile.user_id,
        "instagram_username": profile.username,
        "instagram_followers": profile.followers,
        "platforms": _with_instagram_link(creator.platforms, profile.username),
    })
    return _to_response(creator)


@router.delete("/me/instagram", response_model=CreatorResponse)
def disconnect_my_instagram(
    current_user: CurrentUser = Depends(require_role("creator")), db: Session = Depends(get_db)
):
    """"Desconectar Instagram": apaga do perfil tudo o que veio do Instagram (id da conta, @, seguidores e o link
    confirmado). Nada fica guardado em outro lugar — o token nunca foi salvo —, e a conta fica livre para confirmar
    outro perfil. Para o Instagram deixar de listar o app, o creator remove o acesso em "Apps e sites" de lá."""
    creator = CreatorRepository.get_by_user_id(db, current_user.user_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Perfil de creator não encontrado")

    creator = CreatorRepository.update(db, creator, {
        "instagram_user_id": "",
        "instagram_username": "",
        "instagram_followers": 0,
        "platforms": _with_instagram_link(creator.platforms, ""),
    })
    return _to_response(creator)


@router.post("/{creator_id}/view", response_model=CreatorResponse)
def record_profile_view(
    creator_id: str,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """Chamado quando uma empresa abre o perfil de um creator — cada chamada
    soma 1 na contagem de visualizações (sem deduplicar por empresa/dia:
    reflete literalmente as vezes que o perfil foi aberto)."""
    creator = CreatorRepository.get_by_id(db, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    creator = CreatorRepository.update(db, creator, {"profile_views": (creator.profile_views or 0) + 1})
    return _to_response(creator)


@router.post("/{creator_id}/rating", response_model=CreatorResponse)
def rate_creator(
    creator_id: str,
    data: RatingRequest,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    """Só permite avaliar depois de uma parceria de verdade (proposta aceita)
    entre a empresa e o creator — evita nota avulsa sem nenhuma relação real."""
    creator = CreatorRepository.get_by_id(db, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    if not ProposalRepository.has_accepted_between(db, company.id, creator_id):
        raise HTTPException(
            status_code=403,
            detail="Só é possível avaliar creators com quem você já fechou uma parceria.",
        )

    RatingRepository.upsert(db, company.id, creator_id, data.score)
    creator = CreatorRepository.update(db, creator, {"rating": RatingRepository.average_for_creator(db, creator_id)})
    return _to_response(creator)


@router.get("/{creator_id}/rating/me", response_model=RatingResponse)
def get_my_rating(
    creator_id: str,
    current_user: CurrentUser = Depends(require_role("company")),
    db: Session = Depends(get_db),
):
    company = CompanyRepository.get_by_user_id(db, current_user.user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    rating = RatingRepository.get_by_company_and_creator(db, company.id, creator_id)
    return RatingResponse(score=rating.score if rating else None)


@router.get("/{creator_id}", response_model=CreatorResponse)
def get_creator(creator_id: str, db: Session = Depends(get_db)):
    creator = CreatorRepository.get_by_id(db, creator_id)
    if not creator:
        raise HTTPException(status_code=404, detail="Creator não encontrado")
    return _to_response(creator)
