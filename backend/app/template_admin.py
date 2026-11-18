"""定型文の一覧と削除。登録は、Yesで反映したときに自動で行うため、ここにはない。"""

from typing import Literal

from fastapi import APIRouter, Request

from app.question_admin import ProposalRequest

router = APIRouter(prefix="/api/admin/proposal-templates", tags=["admin-templates"])


@router.get("/{target}")
def list_templates(target: Literal["question", "rule"], request: Request):
    return {"prompts": request.app.state.proposal_reuse.list_prompts(target)}


@router.post("/{target}/remove")
def remove_template(
    target: Literal["question", "rule"], body: ProposalRequest, request: Request
):
    request.app.state.proposal_reuse.remove(target, body.prompt)
    return list_templates(target, request)
