from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['sql', 'clarify', 'refuse']
    sql: str | None = None
    question: str | None = None
    reason: str = Field(min_length=1, max_length=2000)
    metric_ids: list[str] = Field(default_factory=list, max_length=11)

    @model_validator(mode='after')
    def consistent(self):
        if self.action == 'sql' and (not self.sql or self.question is not None):
            raise ValueError('SQL动作必须有SQL且不得有澄清问题')
        if self.action == 'clarify' and (self.sql is not None or not self.question):
            raise ValueError('澄清动作不得携带SQL且必须有具体问题')
        if self.action == 'refuse' and (self.sql is not None or self.question is not None):
            raise ValueError('拒绝动作不得携带SQL或澄清问题')
        return self


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    question: str = Field(min_length=1, max_length=2000)


class FeedbackRequest(BaseModel):
    feedback_type: Literal['correct', 'field', 'filter', 'metric', 'result', 'other']
    comment: str = Field(default='', max_length=2000)
