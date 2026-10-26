from typing import Literal
from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlparse
import re


class Strict(BaseModel):
    model_config = {'extra': 'forbid'}


def safe_url(value):
    if urlparse(value).scheme not in ('http', 'https') or not urlparse(value).hostname:
        raise ValueError('需要 http(s) 证据链接')
    return value


class ContactInput(Strict):
    address: str = Field(max_length=320)
    source_url: str = Field(max_length=2000)
    basis: str = Field(min_length=5, max_length=2000)
    _url = field_validator('source_url')(safe_url)

    @field_validator('address')
    @classmethod
    def email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r'[a-zA-Z0-9.!#$%&\'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+', value):
            raise ValueError('只支持单个有效邮箱地址')
        return value


class DraftInput(Strict):
    contact_id: int
    channel: Literal['simulation', 'smtp'] = 'simulation'


class MessageEdit(Strict):
    version: int
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)
    contact_id: int
    channel: Literal['simulation', 'smtp']
    attachments: list = Field(default_factory=list, max_length=0)

    @field_validator('subject')
    @classmethod
    def single_line(cls, value):
        if '\r' in value or '\n' in value:
            raise ValueError('主题不能换行')
        return value


class Approval(Strict):
    version: int
    content_hash: str


class QueueInput(Strict):
    not_before: str = ''


class Feedback(Strict):
    text: str = Field(max_length=2000)
    human_takeover: bool = False
    status: str = Field(default='待核实', max_length=40)


class Analysis(Strict):
    summary: str = Field(max_length=1000)
    fit: Literal['high', 'medium', 'low', 'unknown']
    priority: Literal['high', 'medium', 'low']
    purchase_stage: Literal['explicit_need', 'exploring', 'unknown']
    recency: str = Field(max_length=200)
    evidence: list[str] = Field(max_length=5)
    unknowns: list[str] = Field(max_length=12)
    next_action: str = Field(max_length=400)
