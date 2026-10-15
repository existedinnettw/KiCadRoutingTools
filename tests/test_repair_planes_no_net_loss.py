#!/usr/bin/env python3
"""repair_planes never ships a pass that lost signal nets when a pass without
pre-existing rips loses fewer (issue #293 follow-up).

Pad repair rips earlier-step signal copper through route.py's ladder. On a
dense 2-layer LQFP board the cascade left 11 previously connected nets broken
while the summary said complete/ok and the CLI exited 0. main() now re-runs the
engine with KICAD_RIP_PREEXISTING=0 when the first pass lost nets and writes the
result that lost fewer; what is still lost is named in the summary.

The engine is stubbed (a real cascade needs a large dense board); the stub
records the env it ran under and the lost nets it reports.

  1. first pass loses nets, retry loses none -> retry written, env restored
  2. retry is not better -> first pass kept, its outcome records restored
  3. first pass loses nothing -> no retry
  4. KICAD_RIP_PREEXISTING=0 already -> no retry
  5. engine resets its per-run outcome records on entry
  6. the retry reads ``retry_input`` (the original board under --overwrite)
  7. the clearance ledger ends at the kept pass's state, and the retry starts
     from the state before the first pass

Run:  python3 tests/test_repair_planes_no_net_loss.py
"""
import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'py_router'))
sys.path.insert(0, os.path.join(ROOT, 'py_tools'))

import clearance_ledger as cl  # noqa: E402
import repair_planes as rp  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    if not ok:
        FAILS.append(f"{name}: {detail}")


def make_stub(outcomes, calls, inputs=None, ledgers=None):
    """outcomes: list of (regressed, still_open, file_text[, clearance]) per call."""
    def stub(input_file, output_file, **kw):
        regressed, still_open, text, *used = outcomes[len(calls)]
        calls.append(os.environ.get('KICAD_RIP_PREEXISTING'))
        if inputs is not None:
            inputs.append(input_file)
        if ledgers is not None:
            ledgers.append(cl.get_min())
        if used:
            cl.record(used[0])
        rp.LAST_REGRESSED_SIGNAL_NETS = list(regressed)
        rp.LAST_RIPPED_STILL_OPEN = list(still_open)
        with open(output_file, 'w') as f:
            f.write(text)
        return (len(calls), 0)
    return stub


def run_case(name, outcomes, env=None, retry_input=None, ledger_before=None, inputs=None, ledgers=None):
    real = rp.repair_planes
    calls = []
    cl.reset()
    cl.record(ledger_before)
    old_env = os.environ.pop('KICAD_RIP_PREEXISTING', None)
    if env is not None:
        os.environ['KICAD_RIP_PREEXISTING'] = env
    try:
        rp.repair_planes = make_stub(outcomes, calls, inputs, ledgers)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, 'b.kicad_pcb')
            args = argparse.Namespace(dry_run=False, input_file=os.path.join(tmp, 'a.kicad_pcb'),
                                      output_file=out)
            first = rp.repair_planes(input_file=args.input_file, output_file=out)
            res = rp._no_net_loss_retry(args, {}, first, retry_input=retry_input,
                                        ledger_before=ledger_before)
            with open(out) as f:
                text = f.read()
            env_after = os.environ.get('KICAD_RIP_PREEXISTING')
        return calls, res, text, env_after
    finally:
        rp.repair_planes = real
        os.environ.pop('KICAD_RIP_PREEXISTING', None)
        if old_env is not None:
            os.environ['KICAD_RIP_PREEXISTING'] = old_env


def main():
    # 1. retry better -> written, records are the retry's, env restored
    calls, res, text, env = run_case('better', [(['/A', '/B'], [], 'first'), ([], [], 'retry')])
    check('better: retry ran with rips off', calls == [None, '0'], calls)
    check('better: retry board written', text == 'retry', text)
    check('better: retry result returned', res == (2, 0), res)
    check('better: records are the retry\'s', rp.LAST_REGRESSED_SIGNAL_NETS == [],
          rp.LAST_REGRESSED_SIGNAL_NETS)
    check('better: env restored', env is None, env)

    # 2. retry not better -> first kept, first's records restored
    calls, res, text, env = run_case('worse', [(['/A'], [], 'first'), (['/A'], ['/C'], 'retry')])
    check('worse: retry ran', calls == [None, '0'], calls)
    check('worse: first board kept', text == 'first', text)
    check('worse: first result returned', res == (1, 0), res)
    check('worse: first records restored',
          rp.LAST_REGRESSED_SIGNAL_NETS == ['/A'] and rp.LAST_RIPPED_STILL_OPEN == [],
          (rp.LAST_REGRESSED_SIGNAL_NETS, rp.LAST_RIPPED_STILL_OPEN))

    # 3. nothing lost -> no retry
    calls, res, text, _ = run_case('clean', [([], [], 'first')])
    check('clean: single pass', calls == [None] and text == 'first', (calls, text))

    # 4. rips already off -> no retry
    calls, res, text, env = run_case('off', [(['/A'], [], 'first')], env='0')
    check('off: single pass', calls == ['0'] and text == 'first', (calls, text))
    check('off: env kept', env == '0', env)

    # 5. the real engine clears its per-run records on entry (it raises on the
    #    missing board right after, which is fine: the reset comes first)
    rp.LAST_REGRESSED_SIGNAL_NETS, rp.LAST_RIPPED_STILL_OPEN = ['/stale'], ['/stale']
    try:
        rp.repair_planes(input_file='/nonexistent.kicad_pcb', output_file='/nonexistent_out.kicad_pcb',
                         net_names=['GND'], plane_layers=['B.Cu'])
    except BaseException:
        pass
    check('reset: records cleared on entry',
          rp.LAST_REGRESSED_SIGNAL_NETS == [] and rp.LAST_RIPPED_STILL_OPEN == [],
          (rp.LAST_REGRESSED_SIGNAL_NETS, rp.LAST_RIPPED_STILL_OPEN))

    # 6. retry reads the snapshot of the original board
    inputs = []
    run_case('snapshot', [(['/A'], [], 'first'), ([], [], 'retry')],
             retry_input='/snap/a.kicad_pcb', inputs=inputs)
    check('snapshot: retry read the original board', inputs[1:] == ['/snap/a.kicad_pcb'], inputs)

    # 7. ledger: retry starts from the pre-run state; the kept pass's state stays
    ledgers = []
    run_case('ledger-retry', [(['/A'], [], 'first', 0.1), ([], [], 'retry', 0.15)],
             ledger_before=0.2, ledgers=ledgers)
    check('ledger: retry started from the pre-run minimum', ledgers == [0.2, 0.2], ledgers)
    check('ledger: discarded first pass\'s 0.1 does not survive', cl.get_min() == 0.15, cl.get_min())
    run_case('ledger-first', [(['/A'], [], 'first', 0.1), (['/A', '/B'], [], 'retry', 0.05)],
             ledger_before=0.2)
    check('ledger: discarded retry\'s 0.05 does not survive', cl.get_min() == 0.1, cl.get_min())

    if FAILS:
        print("FAIL:\n  " + "\n  ".join(FAILS))
        return 1
    print("PASS: repair_planes retries without pre-existing rips and writes the pass that lost fewer nets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
