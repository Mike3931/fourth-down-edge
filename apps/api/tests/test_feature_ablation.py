from fde_api.backtest.feature_ablation import run_ablation
from fde_api.config import settings


def test_exploratory_ablation_retains_trials_and_never_promotes(league_session,tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'artifacts_dir',tmp_path)
    report=run_ablation(league_session,seasons=[2024],source_hash='controlled-fixture')
    assert report['scope']=='EXPLORATORY_REUSED_SEASONS'
    assert len(report['folds'])==2 and report['recommended_for_review']==[]
    for fold in report['folds']:
        assert len(fold['trials'])==2
        assert fold['status']=='RESEARCH ONLY'
        assert fold['metrics']['n']>0
        assert fold['calibrated_moneyline']['log_loss']>0
