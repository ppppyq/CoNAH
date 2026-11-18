"""Check real image input and both bounding-box conventions before navigation."""
import argparse
import base64
import io
import json
from pathlib import Path
import time
import uuid
from PIL import Image, ImageDraw, ImageFont
from comm_harness.planner.harness_vln.ollama_client import OllamaClient


def iou(a, b):
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    return intersection / max(1, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='examples/harness_vln_ollama.json')
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads(Path(args.config).read_text())['planner']
    backend = OllamaClient(config['base_url'], config['num_ctx'], config['num_predict'])
    capabilities = backend.verify_vision(config['model'])
    frame = Image.open(args.image).convert('RGB').resize((640, 480))
    marker = [420, 70, 625, 140]
    nonce = uuid.uuid4().hex[:6].upper()
    draw = ImageDraw.Draw(frame)
    draw.rectangle(marker, fill=(255, 0, 255))
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 34)
    draw.text((430, 82), nonce, font=font, fill=(0, 0, 0))
    frame.save(output / 'probe.png')
    buffer = io.BytesIO()
    frame.save(buffer, format='PNG')
    image_url = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
    report = {'capabilities': capabilities, 'expected_code': nonce, 'marker_px': marker, 'checks': []}
    for convention in ('pixel', 'normalized'):
        units = ('absolute pixels for this 640x480 image' if convention == 'pixel'
                 else 'coordinates normalized to 0-1000 independently for x and y')
        prompt = ('Inspect the attached indoor image. Read the six-character code printed on the '
                  'magenta rectangle, and tightly bound the whole magenta rectangle. '
                  'Return JSON only: {"code":"...","bbox_2d":[x1,y1,x2,y2]}. '
                  'For bbox_2d use ' + units + '. Do not infer the code from context.')
        started = time.monotonic()
        response = backend.create(model=config['model'], messages=[{'role': 'user', 'content': [
            {'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': image_url}}]}],
            response_format={'type': 'json_object'}, temperature=0, max_tokens=256, timeout=300)
        (output / ('response_' + convention + '.txt')).write_text(response.choices[0].message.content)
        answer = json.loads(response.choices[0].message.content)
        bbox = answer['bbox_2d']
        pixels = bbox if convention == 'pixel' else [bbox[0]*.64, bbox[1]*.48, bbox[2]*.64, bbox[3]*.48]
        check = {'convention': convention, 'answer': answer, 'iou': iou(pixels, marker),
                 'iou_as_pixels': iou(bbox, marker),
                 'iou_as_normalized': iou([bbox[0]*.64, bbox[1]*.48, bbox[2]*.64, bbox[3]*.48], marker),
                 'code_correct': str(answer.get('code', '')).strip().upper() == nonce,
                 'elapsed_s': round(time.monotonic() - started, 3),
                 'tokens': response.usage.total_tokens}
        report['checks'].append(check)
        (output / 'result.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(check), flush=True)
    configured_scale = config.get('bbox_scale', 'pixel')
    grounding_check, stop_check = report['checks']
    configured_iou = (grounding_check['iou_as_normalized'] if str(configured_scale) == '1000'
                      else grounding_check['iou_as_pixels'])
    report['bbox_scale'] = configured_scale
    report['passed'] = (all(c['code_correct'] for c in report['checks'])
                        and configured_iou >= .55 and stop_check['iou_as_normalized'] >= .55)
    (output / 'result.json').write_text(json.dumps(report, indent=2))
    if not report['passed']:
        raise SystemExit('Vision/coordinate probe failed; inspect result.json before running navigation')


if __name__ == '__main__':
    main()
