# graph_rag/parsers/dts_parser.py
import subprocess
import yaml
import os
import logging
from typing import Dict, Any

class DTSParser:
    """
    負責將 Device Tree (DTS) 檔案透過 dtc 轉換為 YAML，並解析為圖譜結構。
    Responsible for converting Device Tree (DTS) to YAML via dtc and parsing it into a graph structure.
    """
    def __init__(self):
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
        self.logger = logging.getLogger(__name__)

    def parse(self, dts_path: str) -> Dict[str, Any]:
        """
        解析指定的 DTS 檔案，回傳包含節點與邊緣的字典。
        Parses the specified DTS file and returns a dict with nodes and edges.
        """
        if not os.path.exists(dts_path):
            self.logger.error(f"找不到 DTS 檔案 (DTS file not found): {dts_path}")
            return {"nodes": {}, "edges": []}

        # 1. 呼叫 dtc 將 DTS 轉換為 YAML (Call dtc to convert DTS to YAML)
        yaml_content = self._run_dtc_to_yaml(dts_path)
        if not yaml_content:
            return {"nodes": {}, "edges": []}

        # 2. 解析 YAML 結構 (Parse YAML structure)
        try:
            dts_tree = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            self.logger.error(f"YAML 解析失敗 (YAML parsing failed): {e}")
            return {"nodes": {}, "edges": []}

        graph_data = {
            "nodes": {},
            "edges": []
        }

        # 3. 遞迴走訪硬體樹，提取節點與父子/指標關係 (Recursively traverse the hardware tree)
        # Device Tree 轉換成 YAML 後，通常從最外層的陣列或根節點開始
        # dtc 產生的 YAML 根節點通常是一組陣列，我們遍歷它
        for root_item in dts_tree:
            self._traverse_dts_node(root_item, parent_id="ROOT", graph_data=graph_data)

        self.logger.info(f"DTS 解析完成！共提取 {len(graph_data['nodes'])} 個硬體節點。")
        return graph_data

    def _run_dtc_to_yaml(self, dts_path: str) -> str:
        """執行 dtc 指令 (Executes the dtc command)"""
        try:
            # dtc -I dts -O yaml <file>
            result = subprocess.run(
                ["dtc", "-I", "dts", "-O", "yaml", dts_path],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout
        except subprocess.CalledProcessError as e:
            self.logger.error(f"dtc 編譯失敗 (dtc compilation failed):\n{e.stderr}")
            return ""
        except FileNotFoundError:
            self.logger.error("系統找不到 'dtc' 指令。請確保 Device Tree Compiler 已安裝。 (dtc command not found.)")
            return ""

    def _traverse_dts_node(self, node: Any, parent_id: str, graph_data: Dict[str, Any]):
        """
        遞迴解析 YAML 節點，提取 compatible、暫存器 (reg) 與指標 (phandle) 關係。
        Recursively parses YAML nodes to extract compatible, reg, and phandle relations.
        """
        if not isinstance(node, dict):
            return

        for node_name, attributes in node.items():
            # dtc 產生的 YAML 中，有時會包含 phandle 指標陣列，這裡過濾掉非字典的子節點
            if not isinstance(attributes, dict):
                continue

            # 將節點名稱清理為唯一的 ID (例如: i2c@40005400)
            node_id = f"DTS_{node_name}"
            
            node_props = {
                "type": "hardware_node",
                "compatible": attributes.get("compatible", []),
                "status": attributes.get("status", [["okay"]])[0][0] if attributes.get("status") else "okay",
                # 將其他屬性存為 string，供檢索使用
                "properties": list(attributes.keys()) 
            }
            
            graph_data["nodes"][node_id] = node_props

            # 建立父子結構的邊界 (Parent-Child relationship)
            if parent_id != "ROOT":
                graph_data["edges"].append((parent_id, node_id, "contains"))

            # 尋找指標關係 (例如插斷控制器 interrupt-parent 或 GPIO 引用)
            # 在 dtc 產生的 yaml 中，指標通常以陣列形式存在
            for attr_key, attr_val in attributes.items():
                if isinstance(attr_val, list) and len(attr_val) > 0:
                    # 遞迴處理該屬性底下的子節點 (Child nodes inside current node)
                    if isinstance(attr_val[0], dict):
                        self._traverse_dts_node({node_name: attr_val[0]}, node_id, graph_data)
                    # 這是一個簡化的處理，實際 Zephyr dtc yaml 結構可能更深，
                    # 但這足以抓取父子階層 (Parent-child hierarchy)

            # 遞迴子節點
            self._traverse_dts_node(attributes, node_id, graph_data)

# ==========================================
# 測試區塊 (Testing Block)
# ==========================================
if __name__ == "__main__":
    import json
    
    # 在無法保證測試環境有 dtc 的情況下，我們寫一個繼承類別來「模擬」 dtc 輸出的 YAML
    # To test without requiring dtc installed, we mock the YAML output
    class MockDTSParser(DTSParser):
        def _run_dtc_to_yaml(self, dts_path: str) -> str:
            return """
- "/":
    compatible:
      - "st,stm32f4"
    soc:
      compatible:
        - "simple-bus"
      i2c@40005400:
        compatible:
          - "st,stm32-i2c-v2"
        status:
          - - "okay"
        reg:
          - - 1073763328
            - 1024
        sensor@50:
          compatible:
            - "bosch,bme280"
          reg:
            - - 80
"""

    parser = MockDTSParser()
    # 這裡塞一個假的檔名，因為我們覆蓋了 _run_dtc_to_yaml
    result = parser.parse("dummy.dts") 

    print("\n=== DTS 圖譜邊界 (DTS Graph Edges) ===")
    for edge in result["edges"]:
        print(f"{edge[0]} --[{edge[2]}]--> {edge[1]}")

    print("\n=== DTS 節點詳細資訊 (Node Details - DTS_i2c@40005400) ===")
    print(json.dumps(result["nodes"].get("DTS_i2c@40005400", {}), indent=2))