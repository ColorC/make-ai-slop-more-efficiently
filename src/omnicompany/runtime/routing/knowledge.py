# [OMNI] origin=claude-code domain=omnicompany/runtime ts=2026-04-08T03:23:44Z
# [OMNI] material_id="material:routing.knowledge_injection.router.py"
"""KnowledgeRouter — 知识注入节点基类（V1.3）

知识节点不做实际处理，只将 DESCRIPTION 中的知识内容
附加到数据流中（_knowledge 字段），供下游节点参考。

用法：
    子类只需设置 FORMAT_IN、FORMAT_OUT、DESCRIPTION。
    DESCRIPTION 就是知识内容本身。

示例::

    class P4WorkflowKnowledge(KnowledgeRouter):
        FORMAT_IN  = "p4-changelist"
        FORMAT_OUT = "p4-changelist"
        DESCRIPTION = '''
        P4 提交流程：
        1. p4 edit 打开文件编辑
        2. 修改文件
        3. p4 submit -d "描述" 提交
        注意：必须先 p4 login
        '''
"""

from __future__ import annotations

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router


class KnowledgeRouter(Router):
    """知识注入节点基类。

    子类只需设置 FORMAT_IN、FORMAT_OUT、DESCRIPTION。
    run() 透传输入并附加 _knowledge 字段。
    """

    PASSTHROUGH = True
    INPUT_KEYS = None    # 接受任意输入
    OUTPUT_KEYS = None   # 输出 = 输入 + _knowledge

    async def run(self, input_data: dict) -> Verdict:  # type: ignore[override]
        """透传输入，附加知识内容。"""
        if isinstance(input_data, dict):
            output = {**input_data, "_knowledge": self.DESCRIPTION}
        else:
            output = {"_original": input_data, "_knowledge": self.DESCRIPTION}
        return Verdict(kind=VerdictKind.PASS, output=output)
