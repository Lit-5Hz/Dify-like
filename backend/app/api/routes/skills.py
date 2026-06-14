from __future__ import annotations

import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas import PlatformSkillOut, SkillSynthesizeRequest, SkillValidateRequest, SkillValidationOut
from app.services.platform_skill_service import (
    build_skill_zip,
    delete_skill,
    get_authored_platform_skill,
    get_owned_skill,
    list_platform_skills,
    list_skills,
    list_visible_skills,
    publish_skill,
    revoke_platform_skill,
    synthesize_skill,
    validate_skill_by_id,
    validate_skill_files,
)


router = APIRouter(tags=["skills"])


@router.get("/skills", response_model=list[PlatformSkillOut])
def list_private_skills(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return list_skills(db, current_user.id)


@router.get("/skills/platform", response_model=list[PlatformSkillOut])
def list_public_platform_skills(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return list_platform_skills(db)


@router.get("/skills/visible", response_model=list[PlatformSkillOut])
def list_visible_skill_registry(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return list_visible_skills(db, current_user.id)


@router.post("/skills/validate", response_model=SkillValidationOut)
def validate_skill(
    payload: SkillValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.skill_id:
        skill = get_owned_skill(db, payload.skill_id, current_user.id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found")
        return validate_skill_by_id(db, skill)
    return validate_skill_files(db, current_user.id, payload.files)


@router.post("/skills/synthesize", response_model=PlatformSkillOut)
def synthesize(
    payload: SkillSynthesizeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return synthesize_skill(db, current_user.id, payload)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills/{skill_id}/publish", response_model=PlatformSkillOut)
def publish_private_skill(skill_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return publish_skill(db, skill_id, current_user.id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/skills/{skill_id}/revoke", response_model=PlatformSkillOut)
def revoke_skill(skill_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return revoke_platform_skill(db, skill_id, current_user.id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/skills/{skill_id}", response_model=PlatformSkillOut)
def get_private_skill(skill_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    skill = get_owned_skill(db, skill_id, current_user.id)
    if not skill:
        skill = get_authored_platform_skill(db, skill_id, current_user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.get("/skills/{skill_id}/download")
def download_private_skill(skill_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    skill = get_owned_skill(db, skill_id, current_user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    try:
        content = build_skill_zip(skill)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"{skill.name.replace(' ', '_')}-{skill.id[:8]}.zip"
    ascii_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("_") or f"skill-{skill.id[:8]}.zip"
    return Response(
        content,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; filename*=UTF-8\'\'{quote(filename)}'
            )
        },
    )


@router.delete("/skills/{skill_id}")
def delete_private_skill(skill_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    skill = get_owned_skill(db, skill_id, current_user.id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    delete_skill(db, skill)
    return {"ok": True}
