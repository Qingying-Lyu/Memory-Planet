"""Synthetic integration probe of the production question contract."""
import json
from .evaluate_models import OUT, TEXT
from .cloud import Starflow
from .settings import configure
from .course_schema import Questions, validate_evidence

def run():
    configure()
    value,meta=Starflow().json('qwen3.8-max',
        '只依据原文出2题：一道应用单选，一道简答。引用逐字连续原文，单选4个互异选项，简答量表权重合计100。输出JSON实际数据不是schema。严格符合schema：'+json.dumps(Questions.model_json_schema(),ensure_ascii=False),
        {'blocks':{1:TEXT},'concept_ids':['c1','c2'],'task':'生成题目，题目concept_id只能为c1或c2。'})
    questions=Questions.model_validate(value)
    for q in questions.questions:validate_evidence(q.evidence,{1:TEXT})
    blind=[q.model_dump(exclude={'answer_index','reference_answer','explanation','rubric'}) for q in questions.questions]
    review,rm=Starflow().json('glm-5.3','只依据原文独立作答审题。输出JSON {reviews:[{id,answer_index(简答null),grounded:布尔,unambiguous:布尔,answer:字符串}]}。题干条件不足、多答案、资料不支持必须拒绝。',{'blocks':{1:TEXT},'questions':blind})
    rows={r['id']:r for r in review['reviews']}
    checks={'exactly_two':len(questions.questions)==2,'kinds':{q.kind for q in questions.questions}=={'single_choice','short_answer'},'schema_and_quotes':True,'blind_review':all(rows[q.id].get('grounded') is True and rows[q.id].get('unambiguous') is True and (q.kind!='single_choice' or rows[q.id]['answer_index']==q.answer_index) for q in questions.questions)}
    result={'checks':checks,'questions':value,'review':review,'calls':[meta,rm]}
    (OUT/'question-contract.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checks':checks,'calls':[meta,rm]},ensure_ascii=False))

if __name__=='__main__':
    try:run()
    except Exception as error:
        OUT.mkdir(exist_ok=True)
        result={'model':'qwen3.8-max','status':'failed','error_type':type(error).__name__,
                'error':str(error) if isinstance(error,RuntimeError) else 'contract validation failed'}
        (OUT/'question-contract.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))
        raise SystemExit(1)
