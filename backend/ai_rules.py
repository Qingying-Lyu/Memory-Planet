"""Versioned routing rules for local orchestration. Agents are bounded task roles."""
from dataclasses import dataclass,asdict

VERSION='2026-10-16.angles-v1'
CONCURRENCY=3
TEXT_BATCH_CHARS=4000
EVIDENCE_REVIEW_ITEMS=6
RELATION_BATCH_ITEMS=3

@dataclass(frozen=True)
class Rule:
    agent: str
    model: str
    read_timeout: int
    output_tokens: int
    transport_attempts: int=2
    format_attempts: int=2

RULES={
 'angle_extract':Rule('独立考查角度提取','mimo-v2.6-pro',120,12000),
 'angle_review':Rule('考查角度独立审核','glm-5.3',120,12000),
 'extract':Rule('知识点提取','mimo-v2.6-pro',120,12000),
 'anchor_repair':Rule('引用修复','mimo-v2.6-pro',120,12000),
 'evidence_review':Rule('原文独立审核','glm-5.3',120,12000),
 'relations_batch':Rule('知识点关系初判','mimo-v2.6-flash',60,4096),
 'relations_reconsider':Rule('争议关系复核','mimo-v2.6-pro',120,12000),
 'relations_review':Rule('知识点关系独立审核','glm-5.3',120,12000),
 'vision':Rule('图文提取','mimo-v2.6-pro',120,12000),
}

def rule_for(stage):
    if stage.startswith('evidence_review'):stage='evidence_review'
    if stage.startswith(('relations_batch_review','relations_final_review')):stage='relations_review'
    return RULES[stage]

def client_for(stage,factory):
    rule=rule_for(stage);client=factory()
    client.read_timeout=rule.read_timeout
    client.output_tokens=rule.output_tokens
    client.transport_attempts=rule.transport_attempts
    return client,rule

def describe():return {'version':VERSION,'concurrency':CONCURRENCY,'text_batch_chars':TEXT_BATCH_CHARS,'evidence_review_items':EVIDENCE_REVIEW_ITEMS,'relation_batch_items':RELATION_BATCH_ITEMS,'rules':{name:asdict(rule) for name,rule in RULES.items()}}
