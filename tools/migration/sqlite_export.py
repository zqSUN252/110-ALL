"""Source-owner-declared frozen SQLite bundle acquisition; no live/QA auto-discovery."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'backend'))
from app.migration.sqlite_source import consistent_snapshot,export_snapshot,validate_layout,checked_path
from app.settings import load_settings


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--manifest',required=True);p.add_argument('--snapshot',required=True);p.add_argument('--output',required=True);p.add_argument('--classification',choices=['synthetic','production_supplied'],required=True);a=p.parse_args()
    try:
        m=json.loads(Path(a.manifest).read_text('utf-8'))
        root=checked_path(load_settings().private_root)
        if not all(checked_path(path).is_relative_to(root) and checked_path(path)!=root for path in (a.snapshot,a.output)):
            raise ValueError('Private controlled output paths required')
        validate_layout(a.source,a.snapshot,a.output,m)
        acquired=consistent_snapshot(a.source,a.snapshot,frozen_manifest=m)
        result=export_snapshot(a.snapshot,a.output,a.classification,m['source'],m.get('attachments'),m.get('attachment_root'))
        print(json.dumps({'acquired':acquired,'export':result}))
    except Exception as error:print(json.dumps({'state':'failed','error_type':type(error).__name__}),file=sys.stderr);sys.exit(1)
