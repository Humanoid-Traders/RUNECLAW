"""
RUNECLAW Full Skill Test Harness — exercises every skill against live Bitget data.
Reports: PASS / FAIL / ERROR for each skill with output excerpts.
"""
import asyncio, sys, os, time, traceback
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot.core.engine import RuneClawEngine
from bot.skills.skill_registry import build_default_registry

# Skills that need special args or should be skipped
SKIP_SKILLS = {"halt", "kill_switch"}  # destructive — don't trip circuit breaker during test
SYMBOL_SKILLS = {"analyze_asset", "check_event_risk", "quant_analyze", "explain_trade"}
TRADE_SKILLS = {"execute_paper_trade", "request_live_approval"}  # need pending trade


async def main():
    print("=" * 64)
    print("  RUNECLAW FULL SKILL TEST — LIVE BITGET DATA")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print("=" * 64)

    engine = RuneClawEngine()
    registry = build_default_registry()
    skills = registry.list_skills()

    results = {"PASS": [], "FAIL": [], "ERROR": [], "SKIP": []}

    for skill_line in skills:
        name = skill_line.split(" --")[0].strip()

        if name in SKIP_SKILLS:
            results["SKIP"].append(name)
            print(f"\n  [{len(results['PASS'])+len(results['FAIL'])+len(results['ERROR'])+len(results['SKIP'])}/{len(skills)}] {name}: SKIP (destructive)")
            continue

        if name in TRADE_SKILLS:
            results["SKIP"].append(name)
            print(f"\n  [{len(results['PASS'])+len(results['FAIL'])+len(results['ERROR'])+len(results['SKIP'])}/{len(skills)}] {name}: SKIP (needs pending trade)")
            continue

        kwargs = {}
        if name in SYMBOL_SKILLS:
            kwargs["symbol"] = "BTC/USDT"

        idx = len(results['PASS'])+len(results['FAIL'])+len(results['ERROR'])+len(results['SKIP'])+1
        print(f"\n  [{idx}/{len(skills)}] {name}...", end="", flush=True)

        try:
            skill = registry.get(name)
            if skill is None:
                results["ERROR"].append((name, "Skill not found in registry"))
                print(f" ERROR (not found)")
                continue

            output = await asyncio.wait_for(
                skill.execute(engine, **kwargs),
                timeout=60.0,
            )

            if output and len(str(output)) > 0:
                out_str = str(output)
                # Check for error indicators in output
                if "error" in out_str.lower() and "no error" not in out_str.lower():
                    results["FAIL"].append((name, out_str[:200]))
                    print(f" FAIL")
                    print(f"    >>> {out_str[:150]}")
                else:
                    results["PASS"].append((name, len(out_str)))
                    print(f" PASS ({len(out_str)} chars)")
                    # Print first meaningful line
                    first_lines = [l for l in out_str.split("\n") if l.strip()][:3]
                    for l in first_lines:
                        print(f"    >>> {l[:120]}")
            else:
                results["FAIL"].append((name, "Empty output"))
                print(f" FAIL (empty output)")

        except asyncio.TimeoutError:
            results["ERROR"].append((name, "TIMEOUT (60s)"))
            print(f" ERROR (timeout)")
        except Exception as exc:
            tb = traceback.format_exc()
            results["ERROR"].append((name, f"{exc}\n{tb[-300:]}"))
            print(f" ERROR: {exc}")

    # Summary
    print("\n" + "=" * 64)
    print("  RESULTS SUMMARY")
    print("=" * 64)
    print(f"  PASS  : {len(results['PASS'])}")
    print(f"  FAIL  : {len(results['FAIL'])}")
    print(f"  ERROR : {len(results['ERROR'])}")
    print(f"  SKIP  : {len(results['SKIP'])}")
    print(f"  TOTAL : {len(skills)}")

    if results["PASS"]:
        print(f"\n  PASSED SKILLS:")
        for name, size in results["PASS"]:
            print(f"    ✓ {name} ({size} chars)")

    if results["FAIL"]:
        print(f"\n  FAILED SKILLS:")
        for name, reason in results["FAIL"]:
            print(f"    ✗ {name}: {reason[:120]}")

    if results["ERROR"]:
        print(f"\n  ERRORED SKILLS:")
        for name, reason in results["ERROR"]:
            print(f"    ✗ {name}: {str(reason)[:120]}")

    if results["SKIP"]:
        print(f"\n  SKIPPED SKILLS:")
        for name in results["SKIP"]:
            print(f"    - {name}")

    await engine.stop()
    print("\n" + "=" * 64)
    print("  Test complete.")
    print("=" * 64)

if __name__ == "__main__":
    asyncio.run(main())
