"""AI Command Center explanations, written from the engine's measured evidence.

No external AI model is called. Each sentence is filled in from stored metrics and the rule
outcome that produced the recommendation, so every number in an explanation comes from the
loaded dataset.
"""
from .pricing import inr

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _working_hours_text(wh: dict) -> str:
    wd = sorted(wh["weekdays"])
    span = f"{DAYS[wd[0]]}-{DAYS[wd[-1]]}" if wd else "all days"
    return f"{wh['start_hour']:02d}:00-{wh['end_hour']:02d}:00, {span}, IST"


def _decision_sentence(rec: dict) -> str:
    conf, risk = rec["confidence"], rec["risk"]
    t = rec["type"]
    if t == "rightsize":
        return (f"The engine suggests moving from {rec['current']} to {rec['proposed']}, an estimated saving of "
                f"{inr(rec['savings_monthly'])} a month, because the peak-load and memory checks passed "
                f"(confidence {conf:.0f}%, risk {risk}).")
    if t == "idle_stop_test":
        return (f"Because CPU stays this low, the VM is a candidate for a stop test; its storage and public IP "
                f"would keep billing (confidence {conf:.0f}%, risk {risk}).")
    if t == "schedule_off_hours":
        return (f"Its load outside working hours is low on a non-production environment, so switching it off "
                f"then saves an estimated {inr(rec['savings_monthly'])} a month (confidence {conf:.0f}%, risk {risk}).")
    if t == "collect_more_data":
        return ("There are too few valid measurements to judge this resource, so the engine asks for more data "
                "instead of claiming a saving.")
    # Orphaned volumes and public IPs: state the engine's own finding, and that it is review-only.
    first = rec["explanation"].split(". ")[0].rstrip(".")
    return f"{first}. Nothing is removed automatically; review it first."


def explain_recommendation(rec: dict, profile, working_hours: dict) -> list:
    """Three to four plain sentences explaining why this resource was recommended."""
    lines = []
    if profile is not None:
        cpu, mem = profile["cpu"], profile["mem"]
        lines.append(f"CPU averaged {cpu['mean']:.1f}% over the observation window, with a 95th percentile of "
                     f"{cpu['p95']:.1f}% and a peak of {cpu['max']:.1f}%.")
        lines.append(f"During working hours ({_working_hours_text(working_hours)}) its 95th-percentile CPU was "
                     f"{profile['cpu_business_p95']:.1f}%, and outside those hours it was "
                     f"{profile['cpu_offhours_p95']:.1f}%.")
        lines.append(f"Memory held at a 99th percentile of {mem['p99']:.1f}% with a peak of {mem['max']:.1f}%.")
    else:
        lines.extend(rec["evidence"][:2])
    lines.append(_decision_sentence(rec))
    return lines
