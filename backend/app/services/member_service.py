from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.text import slugify
from app.models.member import MemberProfile
from app.models.skill import MemberSkill, Skill
from app.models.user import User
from app.schemas.member import (
    MemberCreate,
    MemberRead,
    MemberSkillRead,
    MemberUpdate,
    SkillInput,
)


async def get_or_create_user(session: AsyncSession, discord_user_id: str, username: str) -> User:
    result = await session.execute(sa.select(User).where(User.discord_user_id == discord_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(discord_user_id=discord_user_id, username=username)
        session.add(user)
        await session.flush()
    elif username and user.username != username:
        user.username = username
    return user


async def get_member(
    session: AsyncSession, server_id: uuid.UUID, discord_user_id: str
) -> MemberProfile | None:
    from sqlalchemy.orm import selectinload
    result = await session.execute(
        sa.select(MemberProfile)
        .options(selectinload(MemberProfile.user), selectinload(MemberProfile.skills).selectinload(MemberSkill.skill))
        .join(User, User.id == MemberProfile.user_id)
        .where(MemberProfile.server_id == server_id, User.discord_user_id == discord_user_id)
    )
    return result.scalar_one_or_none()


async def require_member(
    session: AsyncSession, server_id: uuid.UUID, discord_user_id: str
) -> MemberProfile:
    member = await get_member(session, server_id, discord_user_id)
    if member is None:
        raise NotFoundError(
            f"No member profile for Discord user {discord_user_id} in this server",
            details={"discord_user_id": discord_user_id},
        )
    return member


async def get_member_by_id(
    session: AsyncSession, server_id: uuid.UUID, member_id: uuid.UUID
) -> MemberProfile:
    result = await session.execute(
        sa.select(MemberProfile).where(
            MemberProfile.id == member_id, MemberProfile.server_id == server_id
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError("Member not found in this Discord server")
    return member


async def list_members(session: AsyncSession, server_id: uuid.UUID) -> list[MemberProfile]:
    result = await session.execute(
        sa.select(MemberProfile)
        .where(MemberProfile.server_id == server_id, MemberProfile.is_active.is_(True))
        .order_by(MemberProfile.display_name)
    )
    return list(result.scalars())


async def get_or_create_skill(session: AsyncSession, name: str, category: str | None) -> Skill:
    """Skills are a shared vocabulary, keyed by slug so 'React' and 'react'
    resolve to the same row."""
    slug = slugify(name)
    result = await session.execute(sa.select(Skill).where(Skill.slug == slug))
    skill = result.scalar_one_or_none()
    if skill is None:
        skill = Skill(slug=slug, name=name.strip(), category=category)
        session.add(skill)
        await session.flush()
    elif category and not skill.category:
        skill.category = category
    return skill


async def set_member_skills(
    session: AsyncSession,
    member: MemberProfile,
    skills: list[SkillInput],
    *,
    replace: bool = False,
) -> MemberProfile:
    if replace:
        await session.execute(
            sa.delete(MemberSkill).where(MemberSkill.member_profile_id == member.id)
        )
        await session.flush()

    existing = {
        ms.skill_id: ms
        for ms in (
            await session.execute(
                sa.select(MemberSkill).where(MemberSkill.member_profile_id == member.id)
            )
        ).scalars()
    }

    for entry in skills:
        skill = await get_or_create_skill(session, entry.name, entry.category)
        link = existing.get(skill.id)
        if link is None:
            session.add(
                MemberSkill(
                    member_profile_id=member.id,
                    skill_id=skill.id,
                    proficiency=entry.proficiency,
                    years_experience=entry.years_experience,
                    last_used_on=entry.last_used_on,
                    source=entry.source,
                )
            )
        else:
            link.proficiency = entry.proficiency
            link.years_experience = entry.years_experience or link.years_experience
            link.last_used_on = entry.last_used_on or link.last_used_on
            link.source = entry.source

    await session.flush()
    await session.refresh(member, attribute_names=["skills"])
    return member


async def create_or_update_member(
    session: AsyncSession, server_id: uuid.UUID, payload: MemberCreate
) -> MemberProfile:
    user = await get_or_create_user(session, payload.discord_user_id, payload.username)
    member = await get_member(session, server_id, payload.discord_user_id)

    if member is None:
        member = MemberProfile(
            server_id=server_id,
            user_id=user.id,
            display_name=payload.display_name or payload.username,
            role=payload.role,
            seniority=payload.seniority,
            years_experience=payload.years_experience,
            timezone=payload.timezone,
            availability=payload.availability,
            max_concurrent_tasks=payload.max_concurrent_tasks,
        )
        session.add(member)
        await session.flush()
    else:
        if payload.display_name:
            member.display_name = payload.display_name
        for field in ("role", "seniority", "years_experience", "timezone", "max_concurrent_tasks"):
            value = getattr(payload, field)
            if value is not None:
                setattr(member, field, value)
        member.availability = payload.availability

    if payload.skills:
        await set_member_skills(session, member, payload.skills)
    member.user = user
    return member


async def update_member(
    session: AsyncSession, member: MemberProfile, payload: MemberUpdate
) -> MemberProfile:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    await session.flush()
    return member


def to_member_read(member: MemberProfile) -> MemberRead:
    return MemberRead(
        id=member.id,
        discord_user_id=member.user.discord_user_id if member.user else "",
        display_name=member.display_name,
        role=member.role,
        seniority=member.seniority,
        years_experience=member.years_experience,
        timezone=member.timezone,
        availability=member.availability,
        max_concurrent_tasks=member.max_concurrent_tasks,
        is_active=member.is_active,
        skills=[
            MemberSkillRead(
                slug=ms.skill.slug,
                name=ms.skill.name,
                category=ms.skill.category,
                proficiency=ms.proficiency,
                years_experience=ms.years_experience,
                last_used_on=ms.last_used_on,
                source=ms.source,
            )
            for ms in member.skills
            if ms.skill is not None
        ],
    )
