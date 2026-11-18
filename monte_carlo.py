import numpy as np
from typing import Dict, Any, Optional


# Aylık beklenen getiri (mu) ve volatilite (sigma) profilleri
_ASSET_PROFILES = {
    "PPF": (0.031, 0.004),   # ZBJ / TZL Para Piyasası (istikrarlı nema)
    "TCA": (0.028, 0.042),   # Altın Katılım (enflasyon kalkanı)
    "AFT": (0.034, 0.068),   # Yabancı Teknoloji (yüksek büyüme & kur + volatilite)
    "CASH": (0.000, 0.001),  # Atıl vadesiz nakit
}
_DEFAULT_WEIGHTS = {"PPF": 0.45, "TCA": 0.35, "AFT": 0.20, "CASH": 0.0}


def run_monte_carlo(
    monthly_deposit: float = 5500.0,
    initial_wealth: float = 10000.0,
    target_wealth: float = 650000.0,
    num_simulations: int = 10000,
    total_months: int = 48,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    48 Aylık Mezuniyet Portföyü için 10.000 Yollu Dinamik Monte Carlo Simülasyonu.
    Portföy dağılım ağırlıkları verildiğinde beklenen getiri (mu) ve risk (sigma)
    canlı dağılıma göre harmanlanır; aksi halde %45 PPF / %35 TCA / %20 AFT hedeflenir.
    """
    active_weights = dict(_DEFAULT_WEIGHTS)
    mode = "target_mix"

    if weights:
        tot_w = sum(max(0.0, float(v)) for v in weights.values())
        if tot_w > 0:
            active_weights = {
                k: max(0.0, float(weights.get(k, 0.0))) / tot_w
                for k in _ASSET_PROFILES
            }
            mode = "live_portfolio"

    mu_monthly = sum(active_weights[k] * _ASSET_PROFILES[k][0] for k in _ASSET_PROFILES)
    # Varlıklar arası kısmi korelasyon (0.35) ile gerçekçi portföy standart sapması
    var_sum = sum((active_weights[k] * _ASSET_PROFILES[k][1]) ** 2 for k in _ASSET_PROFILES)
    cov_term = 0.0
    keys = list(_ASSET_PROFILES.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            k1, k2 = keys[i], keys[j]
            cov_term += 2.0 * 0.35 * (active_weights[k1] * _ASSET_PROFILES[k1][1]) * (active_weights[k2] * _ASSET_PROFILES[k2][1])
    sigma_monthly = float(np.sqrt(max(1e-6, var_sum + cov_term)))

    drift = mu_monthly - 0.5 * (sigma_monthly ** 2)
    random_shocks = np.random.normal(drift, sigma_monthly, (num_simulations, total_months))
    growth_factors = np.exp(random_shocks)

    wealth = np.zeros((num_simulations, total_months))
    current_w = np.full(num_simulations, float(initial_wealth))

    for t in range(total_months):
        if t > 0:
            current_w = (current_w + monthly_deposit) * growth_factors[:, t]
        else:
            current_w = current_w * growth_factors[:, t]
        wealth[:, t] = current_w

    milestone_months = [1, 6, 12, 24, 36, 48]
    milestone_labels = ['1. AY', '6. AY', '1. YIL', '2. YIL', '3. YIL', '4. YIL (MEZUNİYET)']

    invested_curve = []
    p10_curve = []
    p50_curve = []
    p90_curve = []

    for m in milestone_months:
        idx = m - 1
        inv = initial_wealth + (m - 1) * monthly_deposit
        invested_curve.append(int(round(inv)))
        p10_curve.append(int(round(np.percentile(wealth[:, idx], 10))))
        p50_curve.append(int(round(np.percentile(wealth[:, idx], 50))))
        p90_curve.append(int(round(np.percentile(wealth[:, idx], 90))))

    final_wealths = wealth[:, -1]
    success_count = np.sum(final_wealths >= target_wealth)
    success_prob_pct = round(float(success_count / num_simulations * 100.0), 1)

    return {
        "monthly_deposit": round(monthly_deposit, 2),
        "initial_wealth": round(initial_wealth, 2),
        "target_wealth": round(target_wealth, 2),
        "months": milestone_months,
        "labels": milestone_labels,
        "invested": invested_curve,
        "p10_bear": p10_curve,
        "p50_median": p50_curve,
        "p90_bull": p90_curve,
        "success_probability_pct": success_prob_pct,
        "portfolio_params": {
            "mode": mode,
            "mu_monthly_pct": round(mu_monthly * 100.0, 2),
            "sigma_monthly_pct": round(sigma_monthly * 100.0, 2),
            "weights_pct": {k: round(v * 100.0, 1) for k, v in active_weights.items()},
        },
        "stats": {
            "worst_case_p10": p10_curve[-1],
            "expected_p50": p50_curve[-1],
            "best_case_p90": p90_curve[-1],
            "total_invested": invested_curve[-1]
        }
    }
