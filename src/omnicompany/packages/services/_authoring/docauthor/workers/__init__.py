# [OMNI] origin=ai-ide domain=services/docauthor/workers ts=2026-05-04T18:10:00Z type=config belongs_to_service=docauthor
# [OMNI] material_id="material:authoring.docauthor.workers_aggregate.exports.py"
"""docauthor Workers (Phase B+ 扩到 4 kind: manifest / design / readme / skill).

- ManifestAuthorWorker: manifest-request → manifest-draft
- DesignDocAuthorWorker: design-request → design-draft
- ReadmeAuthorWorker: readme-request → readme-draft (2026-05-04 加, 自我叙事三件套)
- SkillAuthorWorker: skill-request → skill-draft (2026-05-04 加, 自我叙事三件套)
- DocReviewerWorker: [manifest/design/readme/skill]-draft (OR) → review-verdict
- ManifestRefineRelauncher: review-verdict → manifest-request (子 job · 条件 emit)
- DesignRefineRelauncher: review-verdict → design-request (子 job · 条件 emit)
- FinalLanderWorker: review-verdict → job-final + 写盘 src/ (条件 emit · 与 Relauncher 互斥)
"""
from .manifest_author import ManifestAuthorWorker
from .design_author import DesignDocAuthorWorker
from .readme_author import ReadmeAuthorWorker
from .skill_author import SkillAuthorWorker
from .reviewer import DocReviewerWorker
from .relauncher import (
    ManifestRefineRelauncher,
    DesignRefineRelauncher,
    ReadmeRefineRelauncher,
    SkillRefineRelauncher,
)
from .final_lander import FinalLanderWorker


__all__ = [
    "ManifestAuthorWorker",
    "DesignDocAuthorWorker",
    "ReadmeAuthorWorker",
    "SkillAuthorWorker",
    "DocReviewerWorker",
    "ManifestRefineRelauncher",
    "DesignRefineRelauncher",
    "ReadmeRefineRelauncher",
    "SkillRefineRelauncher",
    "FinalLanderWorker",
]
