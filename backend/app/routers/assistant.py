"""Insights assistant endpoints (bearer MERCHANT_TOKEN): one Gemini turn per request for AG Studio's agents.

The demo's merchant token is public on purpose, so this is treated as a public endpoint that costs money per call:
a fixed model, a size limit, a per-address rate limit and a ceiling per UTC day kept in the database.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..assistant import AssistantError, TurnModel, run_turn
from ..config import settings
from ..db import get_db
from ..deps import get_turn_model, require_merchant
from ..models import AssistantUsage
from ..ratelimit import SlidingWindow, client_ip

router = APIRouter(prefix="/merchant/assistant", tags=["assistant"], dependencies=[Depends(require_merchant)])

MAX_BODY_BYTES = 1_500_000
per_ip = SlidingWindow()


class ToolIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    description: str = Field(default="", max_length=20_000)
    parameters: dict = Field(default_factory=dict)


class ToolChoiceIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=64)


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input: list[dict] = Field(min_length=1, max_length=600)
    instructions: str = Field(default="", max_length=80_000)
    tools: list[ToolIn] = Field(default_factory=list, max_length=64)
    tool_choice: Literal["auto", "none", "required"] | ToolChoiceIn = "auto"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def turns_today(db: Session) -> int:
    return db.execute(select(AssistantUsage.turns).where(AssistantUsage.day == _today())).scalar() or 0


def add_usage(db: Session, turns: int = 0, input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Add to today's row with one UPDATE (so concurrent turns can't lose counts), creating the row on first use."""
    day = _today()
    bump = update(AssistantUsage).where(AssistantUsage.day == day).values(
        turns=AssistantUsage.turns + turns, input_tokens=AssistantUsage.input_tokens + input_tokens,
        output_tokens=AssistantUsage.output_tokens + output_tokens)
    if db.execute(bump).rowcount == 0:
        try:
            db.add(AssistantUsage(day=day, turns=turns, input_tokens=input_tokens, output_tokens=output_tokens))
            db.commit()
            return
        except IntegrityError:        # another request created today's row a moment ago
            db.rollback()
            db.execute(bump)
    db.commit()


def _off_reason() -> str:
    if not settings.assistant_enabled:
        return "The assistant is switched off on this server (ASSISTANT_ENABLED=false)."
    return "The assistant needs a Gemini key on the server (GEMINI_API_KEY)."


@router.get("/status")
def status(model: TurnModel | None = Depends(get_turn_model)) -> dict:
    """Whether the assistant can answer. No database access: the Insights page asks this on every load."""
    if model is None:
        return {"enabled": False, "reason": _off_reason()}
    return {"enabled": True, "model": model.model}


@router.post("/turn")
def turn(body: TurnIn, request: Request, db: Session = Depends(get_db),
         model: TurnModel | None = Depends(get_turn_model)) -> dict:
    """One model turn. Returns AG Studio's response shape: output items (a message and/or tool calls) and usage."""
    if model is None:
        raise HTTPException(503, _off_reason())
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        raise HTTPException(413, "The conversation is too long for the demo's assistant. Start a new chat.")
    if settings.assistant_turns_per_ip_per_minute > 0:
        wait = per_ip.hit(client_ip(request), settings.assistant_turns_per_ip_per_minute)
        if wait is not None:
            raise HTTPException(429, f"The assistant is working too fast for the demo's rate limit. Try again in {wait} s.",
                                headers={"Retry-After": str(wait)})
    if settings.assistant_turns_per_day > 0 and turns_today(db) >= settings.assistant_turns_per_day:
        raise HTTPException(429, "The demo's assistant has reached today's usage limit. It is back tomorrow (UTC); "
                                 "the dashboard itself keeps working.")
    add_usage(db, turns=1)            # counted before the call: a failed call used quota too

    choice = body.tool_choice if isinstance(body.tool_choice, str) else {"name": body.tool_choice.name}
    try:
        result = run_turn(model, items=body.input, instructions=body.instructions,
                          tools=[t.model_dump() for t in body.tools], tool_choice=choice)
    except AssistantError as e:
        raise HTTPException(e.status, e.message)
    add_usage(db, turns=result.model_calls - 1, input_tokens=result.input_tokens, output_tokens=result.output_tokens)
    return result.response
