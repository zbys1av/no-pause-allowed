"""Rebuild a support report's automatic plan without its video or Qt GUI.

Usage: python -m smartcut.debug_replay SmartVideoCutter-debug.zip
"""
import csv
from dataclasses import asdict
import io
import json
import sys
import zipfile
from .model import Settings,MotionSample,analyze_plan,statistics,split_plan


def replay(path):
    with zipfile.ZipFile(path) as archive:
        data=json.loads(archive.read('report.json'))
        if data.get('schema')!='smartcut-debug' or data.get('schema_version')!=1:
            raise ValueError('Unsupported debug report schema')
        if not data.get('analysis') or not data.get('plan_settings'):
            raise ValueError('This report contains no completed analysis to replay')
        samples=[MotionSample(float(r['time_s']),float(r['score']),bool(int(r['raw_active'])),
                    bool(int(r['transient'])),bool(int(r['persistent'])),r.get('evidence',''),json.loads(r.get('bounds') or 'null'))
                 for r in csv.DictReader(io.StringIO(archive.read('samples.csv').decode('utf-8')))]
    report=analyze_plan(samples,data['video']['duration'],Settings(**data['plan_settings']),data['plan_mode'],data['speed'])
    segments=report.plan
    if data.get('review',{}).get('section_boundaries'):
        segments=split_plan(segments,[t for r in data['review']['intervals'] for t in (r['start'],r['end'])])
    plan=[asdict(s) for s in segments]
    return dict(matches_recorded_automatic_plan=plan==data['automatic_plan'],
                automatic_plan=plan,statistics=statistics(segments),
                merged_bursts=report.merged_bursts,rejected_short=report.rejected_short,
                lost_to_padding=report.lost_to_padding,
                note='Replays temporal decisions from captured evidence; pixel detection requires original media.')


if __name__=='__main__':
    print(json.dumps(replay(sys.argv[1]),indent=2))
