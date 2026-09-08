"""集中定义 AI 命题提示词；不调用模型，也不决定任务状态。"""

import json
from typing import Any

from app.models.ai import AIRoundMode


SYSTEM_PROMPT = """你是程序设计课程教师的 OJ 命题助手。你的输出会被程序直接校验并载入题目编辑器。

只输出一个 JSON 对象，不要输出 Markdown、解释、答案代码或 JSON 之外的文字。JSON 必须且只能包含：
id,title,description,input_description,output_description,samples,constraints,testcases,hint,source,tags,
time_limit,memory_limit,author,difficulty。

字段规则：
1. id 是简洁、稳定、可作为文件标识的非空字符串；必须遵守本轮 operation 给出的 ID 规则。
2. title、description、input_description、output_description、constraints 必须非空，题意不得歧义。
3. samples 和 testcases 都至少一项；每项只能包含字符串 input 和 output，空输入或空输出可用空字符串。
4. 样例和测试点的输出必须严格由题意推出，不添加提示语；覆盖最小值、最大值、典型情况、特殊结构和复杂度区分。
5. time_limit 为正数或 null，memory_limit 为正整数或 null；null 表示继承语言默认限制。
6. hint、source、author、difficulty 是字符串，tags 是字符串数组。
7. 生成新题时不能只改标题、背景或数字，必须形成新的题目目标、输入输出契约和测试点，并使用新的 ID。
8. 修改当前题时保持 ID 不变，并让题面、约束、样例和测试点保持一致。
9. 如果 operation.type 是 create_distinct_problem，即使 requirement 中出现“更难”“升级”等词，
   也必须真正设计另一道题；不能通过只改标题、背景或数字来冒充新题。

在输出前自行检查：字段完整、ID 符合 operation、所有样例可人工计算、测试点与约束一致。"""


def round_instruction(
    mode: AIRoundMode,
    current_problem: dict[str, Any] | None,
    requirement: str,
) -> str:
    """把程序已确定的轮次意图编码成无歧义 JSON 用户消息。

    `revise` 要求复用当前 ID；`new` 明确给出禁止复用的 ID。模型可以参考
    当前题的知识点，但不能把“更难的新题”误解成对旧题做原地编辑。
    """

    current_id = current_problem.get("id") if current_problem else None
    if mode is AIRoundMode.REVISE:
        operation = {
            "type": "revise_current_problem",
            "required_id": current_id,
            "rule": "修改当前题；输出 id 必须等于 required_id。",
        }
    else:
        operation = {
            "type": "create_distinct_problem",
            "forbidden_id": current_id,
            "rule": "另出一道新题；输出 id 必须不同于 forbidden_id，且内容不能只是原题换皮。",
        }
    context = {
        "operation": operation,
        "requirement": requirement,
        "current_problem": current_problem,
    }
    return json.dumps(context, ensure_ascii=False)
