"""Compare translation instructions on observed semantic failure cases."""
import json
import model_benchmark as bench

CASES = [
    {'source': '木村さんに電話を貸してもらいました。', 'expected_relation': '我从木村那里借来电话'},
    {'source': '木村さんに電話を貸してあげました。', 'expected_relation': '我把电话借给木村'},
    {'source': '田中さんに本を読んでもらいました。', 'expected_relation': '田中给我读书'},
    {'source': '嫌なら、無理に続けなくていい。', 'expected_relation': '不愿意就不必勉强继续'},
    {'source': 'くそっ、また失敗した。', 'expected_relation': '保留粗口语气与再次失败含义'},
    {'source': '避妊具を正しく使用してください。', 'expected_relation': '正确使用避孕用品'},
]
PROMPTS = {
    'default': '将以下文本翻译成中文，注意只需要输出翻译后的结果，不要额外解释：\n',
    'faithful': ('请将下面的日语对白忠实翻译为自然的简体中文。准确保留动作执行者、接受者、'
                 '借入和借出方向、否定与时间关系；不要添加原文没有的对象。'
                 '保留原文的粗口和性相关用语，不主动打码或委婉化。只输出译文，不要解释。\n日语：'),
}

if __name__ == '__main__':
    bench.RUN_LABEL = 'prompt-comparison'
    rows = []
    with bench.ascii_root() as root:
        bench.configure_cuda(root)
        with bench.translation_server(root, gpu_layers=99):
            for case in CASES:
                row = dict(case)
                for name, prefix in PROMPTS.items():
                    result, elapsed = bench.completion(prefix + case['source'])
                    row[name] = result['choices'][0]['message']['content']
                    row[name + '_seconds'] = elapsed
                rows.append(row)
    (bench.RESULTS / 'prompt-comparison.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(rows, ensure_ascii=False, indent=2))
