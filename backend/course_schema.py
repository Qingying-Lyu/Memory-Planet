"""Strict contracts: models supply evidence, deterministic code owns scoring."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Evidence(Strict):
    block: int
    quote: str = Field(min_length=5, max_length=600)


class Concept(Strict):
    id: str = Field(pattern=r'^c[0-9]+$')
    title: str = Field(min_length=2)
    summary: str
    evidence: list[Evidence] = Field(min_length=1)


class Framework(Strict):
    concepts: list[Concept] = Field(min_length=3, max_length=20)


class Rubric(Strict):
    id: str
    point: str
    weight: int = Field(gt=0, le=100)
    full: str
    partial: str
    zero: str


class Question(Strict):
    id: str = Field(pattern=r'^q[0-9]+$')
    concept_id: str
    kind: Literal['single_choice', 'short_answer']
    prompt: str = Field(min_length=15)
    options: list[str]
    answer_index: int | None
    reference_answer: str
    explanation: str
    evidence: list[Evidence] = Field(min_length=1)
    rubric: list[Rubric]

    @model_validator(mode='after')
    def check(self):
        if self.kind == 'single_choice':
            if len(self.options) != 4 or len(set(self.options)) != 4 or self.answer_index not in range(4) or self.rubric:
                raise ValueError('选择题必须有4个不同选项、有效答案且无简答量表')
        elif self.options or self.answer_index is not None or not self.rubric or sum(r.weight for r in self.rubric) != 100:
            raise ValueError('简答题无选项，量表权重合计100')
        if len({r.id for r in self.rubric}) != len(self.rubric):
            raise ValueError('量表ID重复')
        return self


class Questions(Strict):
    questions: list[Question] = Field(min_length=1, max_length=10)


def validate_evidence(items, blocks):
    for item in items:
        if item.block not in blocks or item.quote not in blocks[item.block]:
            raise ValueError('原文引用不存在或被改写')


def restore_quote_whitespace(items, blocks):
    """Restore original layout only when every non-whitespace character is unchanged."""
    repairs = []
    for item in items:
        text = blocks.get(item.block, '')
        if item.quote in text:
            continue
        positions = [i for i, char in enumerate(text) if not char.isspace()]
        normalized = ''.join(text[i] for i in positions)
        query = ''.join(char for char in item.quote if not char.isspace())
        start = normalized.find(query) if query else -1
        if start < 0:
            continue
        original = text[positions[start]:positions[start+len(query)-1]+1]
        if len(original) > 600:
            continue
        repairs.append({'block': item.block, 'model_quote': item.quote, 'original_quote': original})
        item.quote = original
    return repairs


class Judgement(Strict):
    id: str
    level: Literal['full', 'partial', 'zero']
    answer_quote: str
    reason: str


class Grade(Strict):
    items: list[Judgement]


def rubric_score(question, grade, answer):
    if question.kind != 'short_answer':
        raise ValueError('非简答题')
    if len(grade.items) != len(question.rubric) or {i.id for i in grade.items} != {r.id for r in question.rubric}:
        raise ValueError('评分点缺失或重复')
    weights = {r.id: r.weight for r in question.rubric}
    total = 0
    for item in grade.items:
        if item.level != 'zero' and (not item.answer_quote or item.answer_quote not in answer):
            raise ValueError('得分没有作答原文证据')
        if item.answer_quote and item.answer_quote not in answer:
            raise ValueError('引用了不存在的作答')
        total += weights[item.id] * {'full': 1, 'partial': .5, 'zero': 0}[item.level]
    return total
