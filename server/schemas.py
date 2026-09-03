"""API 请求体模型（Pydantic）。响应大多直接返回 state/manifest 同构的 dict，不强类型化——
state.json 是权威数据且字段随功能演进，强 schema 反而容易把老任务挡在门外。"""
from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    product_info: str = ""
    brand_name: str = ""
    ad_language: str = ""
    ratio_choice: str | None = None  # label 或别名 1:1 / 4:5 / dual
    title_count: int | None = Field(default=None, ge=1, le=10)


class RunPatch(BaseModel):
    product_info: str | None = None
    brand_name: str | None = None
    ad_language: str | None = None
    ratio_choice: str | None = None
    title_count: int | None = Field(default=None, ge=1, le=10)
    selected_scenes: list[int] | None = None


class Feedback(BaseModel):
    feedback: str = Field(min_length=1)


class JobPatch(BaseModel):
    image_prompt: str | None = None
    copies: list | None = None


class GotoVersion(BaseModel):
    version: int = Field(ge=0)


class CopiesStart(BaseModel):
    title_count: int | None = Field(default=None, ge=1, le=10)


class SceneIds(BaseModel):
    ids: list[int] = Field(min_length=1)


class InAdsPatch(BaseModel):
    in_ads: bool


class ApplyAssets(BaseModel):
    paths: list[str] = Field(min_length=1)


class PromptPut(BaseModel):
    template: str = Field(min_length=1)


class ConfigPut(BaseModel):
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    claude_model: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    image_model: str = ""
