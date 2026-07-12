# graph_rag/parsers/kconfig_parser.py
import kconfiglib
import os
import logging
from typing import Dict, Any, Set, List

class KconfigParser:
    """
    負責解析 Zephyr 的 Kconfig 檔案結構，並萃取符號間的圖狀相依關係。
    Responsible for parsing Zephyr's Kconfig structure and extracting graph-like dependencies between symbols.
    """
    def __init__(self, zephyr_base: str = ""):
        """
        :param zephyr_base: Zephyr 原始碼根目錄 (Zephyr source root directory)
        """
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)
        
        # Zephyr 的 Kconfig 系統強烈依賴環境變數 (例如 source "$(ZEPHYR_BASE)/Kconfig")
        # 這裡設定 srctree 以便 kconfiglib 能正確解析相對路徑
        if zephyr_base:
            os.environ["srctree"] = zephyr_base
            os.environ["ZEPHYR_BASE"] = zephyr_base
            
        # 關閉嚴格模式，避免因為缺少某些特定單板的環境變數而中斷解析
        os.environ["KCONFIG_STRICT"] = "0"

    def parse(self, kconfig_path: str) -> Dict[str, Any]:
        """
        解析指定的 Kconfig 檔案，回傳包含節點 (nodes) 與邊緣 (edges) 的字典。
        Parses the specified Kconfig file and returns a dictionary with nodes and edges.
        """
        if not os.path.exists(kconfig_path):
            self.logger.error(f"找不到 Kconfig 檔案 (Kconfig file not found): {kconfig_path}")
            return {"nodes": {}, "edges": []}

        self.logger.info(f"開始解析 Kconfig 樹 (Starting to parse Kconfig tree): {kconfig_path}")
        
        # warn_to_stderr=False 避免解析時產生大量 Zephyr 特有的警告洗頻
        kconf = kconfiglib.Kconfig(kconfig_path, warn_to_stderr=False)
        
        graph_data = {
            "nodes": {}, # 儲存符號屬性 (Store symbol attributes)
            "edges": []  # 儲存 (來源, 目標, 關係) (Store source, target, relation)
        }

        # 遍歷所有定義的 Kconfig 符號
        for sym in kconf.unique_defined_syms:
            if not sym.name:
                continue

            node_id = f"CONFIG_{sym.name}"
            
            # 1. 取得說明文件 (Extract Help Text)
            help_text = sym.nodes[0].help if sym.nodes and sym.nodes[0].help else "No description available."
            
            # 2. 解析 Selects (後繼相依 - 該符號被啟動時，會連帶啟動哪些符號)
            selects = []
            for select_expr, _ in sym.selects:
                if isinstance(select_expr, kconfiglib.Symbol):
                    target_id = f"CONFIG_{select_expr.name}"
                    selects.append(target_id)
                    # 建立有向邊： node_id --selects--> target_id
                    graph_data["edges"].append((node_id, target_id, "selects"))

            # 3. 解析 Depends On (前驅相依 - 該符號需要哪些符號才能被啟動)
            # direct_dep 是一個 AST 表達式，我們需要遞迴萃取出裡面的變數名稱
            deps = self._extract_symbols_from_expr(sym.direct_dep)
            for dep in deps:
                target_id = f"CONFIG_{dep}"
                # 建立有向邊： node_id --depends_on--> target_id
                graph_data["edges"].append((node_id, target_id, "depends_on"))

            # 4. 儲存節點屬性
            graph_data["nodes"][node_id] = {
                "type": kconfiglib.TYPE_TO_STR[sym.type],
                "help": help_text.strip(),
                "selects": selects,
                "depends_on": [f"CONFIG_{d}" for d in deps]
            }

        self.logger.info(f"解析完成！共提取 {len(graph_data['nodes'])} 個節點，{len(graph_data['edges'])} 條邊界。")
        return graph_data

    def _extract_symbols_from_expr(self, expr) -> Set[str]:
        """
        遞迴解析 kconfiglib 的抽象語法樹 (AST)，將布林邏輯中的符號名稱萃取出來。
        Recursively parses the kconfiglib AST to extract symbol names from boolean logic.
        """
        syms = set()
        if isinstance(expr, kconfiglib.Symbol):
            # 排除常數 'y', 'n', 'm'
            if expr.name and expr.name not in ["y", "n", "m"]:
                syms.add(expr.name)
        elif isinstance(expr, tuple):
            # expr 是一個元組，例如 (kconfiglib.AND, expr1, expr2) 或 (kconfiglib.NOT, expr)
            # 我們只需要取出後面的 expr 繼續遞迴
            for item in expr[1:]:
                syms.update(self._extract_symbols_from_expr(item))
        return syms

# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    # 建立一個測試用的微型 Kconfig 檔案
    # Create a dummy micro Kconfig file for testing
    dummy_kconfig_path = "Kconfig.test"
    with open(dummy_kconfig_path, "w", encoding="utf-8") as f:
        f.write("""
config I2C
    bool "I2C Support"
    help
      Enable I2C bus drivers.

config SENSOR_XYZ
    bool "XYZ Sensor Driver"
    depends on I2C
    select PRINTK
    help
      Enable the XYZ temperature sensor. Requires I2C.

config PRINTK
    bool "Printk support"
    help
      Enable kernel printing.
""")

    parser = KconfigParser()
    result = parser.parse(dummy_kconfig_path)

    print("\n=== 萃取出的圖譜邊界 (Extracted Graph Edges) ===")
    for edge in result["edges"]:
        # 輸出格式: 節點A --[關係]--> 節點B
        print(f"{edge[0]} --[{edge[2]}]--> {edge[1]}")

    print("\n=== 節點詳細資訊 (Node Details - CONFIG_SENSOR_XYZ) ===")
    import json
    print(json.dumps(result["nodes"].get("CONFIG_SENSOR_XYZ", {}), indent=2))

    # 清理測試檔案
    os.remove(dummy_kconfig_path)