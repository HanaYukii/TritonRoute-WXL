import pytest

from tools.difficulty.tritonroute_runner import parse_tritonroute_log


# Realistic log snippet from TritonRoute detailed routing output
SAMPLE_LOG_ITER0 = """\
[INFO DRT-0195]  Number of violations = 2493.
[INFO DRT-0196]    Met spacing: 2437
[INFO DRT-0196]    Met short:   13
[INFO DRT-0196]    Other:       43
"""

SAMPLE_LOG_5ITER = """\
[INFO GRT-0018] Total GR usage: 12.38%
[INFO GRT-0019] Total wire length: 4166 um
[INFO DRT-0195] Iteration 0: Number of violations = 2493.
[INFO DRT-0196]    Met spacing: 2437
[INFO DRT-0196]    Met short:   13
[INFO DRT-0196]    Other:       43
[INFO DRT-0195] Iteration 1: Number of violations = 1800.
[INFO DRT-0196]    Met spacing: 1750
[INFO DRT-0196]    Met short:   10
[INFO DRT-0196]    Other:       40
[INFO DRT-0195] Iteration 2: Number of violations = 1200.
[INFO DRT-0196]    Met spacing: 1160
[INFO DRT-0196]    Met short:   8
[INFO DRT-0196]    Other:       32
[INFO DRT-0195] Iteration 3: Number of violations = 900.
[INFO DRT-0196]    Met spacing: 870
[INFO DRT-0196]    Met short:   5
[INFO DRT-0196]    Other:       25
[INFO DRT-0195] Iteration 4: Number of violations = 700.
[INFO DRT-0196]    Met spacing: 680
[INFO DRT-0196]    Met short:   3
[INFO DRT-0196]    Other:       17
[INFO DRT-0195] Iteration 5: Number of violations = 638.
[INFO DRT-0196]    Met spacing: 620
[INFO DRT-0196]    Met short:   2
[INFO DRT-0196]    Other:       16
"""


class TestParseTritonRouteLog:
    def test_parse_single_iteration(self):
        result = parse_tritonroute_log(SAMPLE_LOG_ITER0)
        assert result["iterations"][0]["drv_total"] == 2493
        assert result["iterations"][0]["shorts"] == 13

    def test_parse_five_iterations(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        assert len(result["iterations"]) == 6  # iter 0-5
        assert result["iterations"][0]["drv_total"] == 2493
        assert result["iterations"][5]["drv_total"] == 638
        assert result["iterations"][0]["shorts"] == 13
        assert result["iterations"][5]["shorts"] == 2

    def test_convergence_rate(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        # (2493 - 638) / 2493 = 74.4%
        assert result["conv_rate"] == pytest.approx(0.744, abs=0.01)

    def test_gr_usage(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        assert result["gr_usage"] == pytest.approx(12.38)

    def test_wire_length(self):
        result = parse_tritonroute_log(SAMPLE_LOG_5ITER)
        assert result["wire_length_um"] == pytest.approx(4166.0)

    def test_empty_log_returns_none_metrics(self):
        result = parse_tritonroute_log("")
        assert result["iterations"] == []
        assert result["conv_rate"] is None
        assert result["gr_usage"] is None
