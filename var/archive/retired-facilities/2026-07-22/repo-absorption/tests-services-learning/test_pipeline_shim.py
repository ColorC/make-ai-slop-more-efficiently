from omnicompany.packages.services._learning.absorption import pipeline


def test_deprecated_pipeline_shim_exports_all_registered_builders():
    assert pipeline.PIPELINES["absorption.survey"] is pipeline.build_survey_pipeline
    assert pipeline.PIPELINES["absorption.v2"] is pipeline.build_v2_pipeline
    assert pipeline.PIPELINES["absorption.v3"] is pipeline.build_v3_pipeline
    assert pipeline.PIPELINES["absorption.v3-stage3"] is pipeline.build_v3_stage3_pipeline
