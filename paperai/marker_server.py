"""常駐的 Marker 程序：載入一次模型，之後從 stdin 接收分段轉檔工作。

每行輸入 {"pdf", "pages", "force_ocr"}，輸出一行 RESULT_PREFIX + {"markdown"} 或 {"error"}。
放在獨立程序，閒置時結束就能釋放 VRAM；父程序結束時 stdin 關閉，這裡也會跟著結束。
"""
import json
import sys

RESULT_PREFIX = "PAPERAI_RESULT "


def main():
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    models = create_model_dict()
    for line in sys.stdin:
        job = json.loads(line)
        try:
            converter = PdfConverter(artifact_dict=models, config={
                "page_range": job["pages"], "force_ocr": job["force_ocr"],
                "disable_image_extraction": True, "disable_tqdm": True})
            result = {"markdown": converter(job["pdf"]).markdown}
        except Exception as e:
            result = {"error": str(e)}
        print(RESULT_PREFIX + json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
