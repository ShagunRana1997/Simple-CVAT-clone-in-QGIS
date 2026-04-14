import os
import glob
import json
import xml.etree.ElementTree as ET
from PyQt5.QtCore import QVariant
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer, QgsFeature, QgsField
)
from qgis.gui import QgsMapToolCapture 
from PyQt5.QtWidgets import (
    QAction, QFileDialog, QToolBar, QMessageBox, QDialog, 
    QVBoxLayout, QComboBox, QDialogButtonBox, QLabel
)
from PyQt5.QtGui import QIcon

from .Anno_tool_dialog import AnnotationDialog

# --- CUSTOM EXPORT DIALOG ---
class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export Annotations (OBB)")
        self.resize(300, 150)
        layout = QVBoxLayout()
        
        # Scope
        layout.addWidget(QLabel("Export Scope:"))
        self.cb_scope = QComboBox()
        self.cb_scope.addItems(["Bulk Export (All Images)", "Current Image Only"])
        layout.addWidget(self.cb_scope)
        
        # Format
        layout.addWidget(QLabel("Export Format:"))
        self.cb_format = QComboBox()
        self.cb_format.addItems(["YOLO OBB (.txt)", "CVAT XML (.xml)"])
        layout.addWidget(self.cb_format)
        
        # Buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

    def get_selections(self):
        return {
            "is_bulk": self.cb_scope.currentIndex() == 0,
            "format": "yolo" if self.cb_format.currentIndex() == 0 else "xml"
        }

# --- CUSTOM MAP TOOL CLASS ---
class SimplePolygonTool(QgsMapToolCapture):
    def __init__(self, canvas, cad_dock, callback):
        super().__init__(canvas, cad_dock, QgsMapToolCapture.CapturePolygon)
        self.callback = callback

    def geometryCaptured(self, geometry):
        self.callback(geometry)

# --- MAIN PLUGIN CLASS ---
class AnnotationTool:
    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.tool = None
        
        self.current_schema = {'classes': ["Unspecified"], 'attributes': {}}
        self.last_defaults = {} 
        self.image_files = []
        self.current_image_index = -1
        self.current_raster_layer = None

    def initGui(self):
        self.toolbar = self.iface.addToolBar("Defense Annotator")
        self.toolbar.setObjectName("DefenseAnnotatorToolbar")

        # 1. Project Management
        self.action_new_project = QAction("📝 Load CVAT Labels", self.iface.mainWindow())
        self.action_new_project.triggered.connect(self.create_project_from_json)
        self.toolbar.addAction(self.action_new_project)

        self.toolbar.addSeparator()

        # 2. Image Handling
        self.action_load_folder = QAction("📂 Load Images", self.iface.mainWindow())
        self.action_load_folder.triggered.connect(self.load_image_folder)
        self.toolbar.addAction(self.action_load_folder)

        self.action_prev = QAction("⬅️ Prev", self.iface.mainWindow())
        self.action_prev.triggered.connect(self.prev_image)
        self.action_prev.setEnabled(False)
        self.toolbar.addAction(self.action_prev)

        self.action_next = QAction("Next ➡️", self.iface.mainWindow())
        self.action_next.triggered.connect(self.next_image)
        self.action_next.setEnabled(False)
        self.toolbar.addAction(self.action_next)

        self.toolbar.addSeparator()

        # 3. Annotate
        self.action_annotate = QAction("✏️ Annotate", self.iface.mainWindow())
        self.action_annotate.triggered.connect(self.run)
        self.toolbar.addAction(self.action_annotate)
        
        self.toolbar.addSeparator()

        # 4. EXPORT
        self.action_export = QAction("🚀 Export Annotations", self.iface.mainWindow())
        self.action_export.triggered.connect(self.export_annotations)
        self.toolbar.addAction(self.action_export)

    def unload(self):
        del self.toolbar

    # --- 1. JSON IMPORT & AUTO LAYER CREATION ---
    def create_project_from_json(self):
        filename, _ = QFileDialog.getOpenFileName(self.iface.mainWindow(), "Select CVAT Label File", "", "Text/JSON Files (*.txt *.json)")
        if not filename: return

        try:
            with open(filename, 'r', encoding='utf-8') as file:
                cvat_data = json.load(file)
                
            classes = []
            attributes = {}

            for label_item in cvat_data:
                class_name = label_item.get("name")
                if class_name: classes.append(class_name)
                for attr_item in label_item.get("attributes", []):
                    attr_name = attr_item.get("name")
                    vals = [v.strip() for v in attr_item.get("values", []) if v.strip()]
                    if attr_name not in attributes:
                        attributes[attr_name] = vals
                    else:
                        attributes[attr_name] = list(set(attributes[attr_name] + vals))

            if classes: self.current_schema['classes'] = sorted(classes)
            self.current_schema['attributes'] = attributes

        except Exception as e:
            self.iface.messageBar().pushMessage("JSON Parse Error", f"Could not read file: {str(e)}", level=2)
            return

        layer = QgsVectorLayer("Polygon?crs=EPSG:4326", "CVAT_Annotations", "memory")
        pr = layer.dataProvider()
        fields = [QgsField("image_name", QVariant.String, len=255), QgsField("Class Name", QVariant.String, len=100)]
        for attr_name in self.current_schema['attributes'].keys():
            fields.append(QgsField(attr_name, QVariant.String, len=100))
        fields.append(QgsField("Remarks", QVariant.String, len=255))
        pr.addAttributes(fields)
        layer.updateFields()
        
        QgsProject.instance().addMapLayer(layer)
        self.iface.setActiveLayer(layer)
        layer.startEditing()
        self.iface.messageBar().pushMessage("Project Created", "Schema loaded successfully.", level=0)

    # --- 2. IMAGE NAVIGATION ---
    def load_image_folder(self):
        folder = QFileDialog.getExistingDirectory(self.iface.mainWindow(), "Select Image Directory")
        if not folder: return

        self.image_files = glob.glob(os.path.join(folder, "*.png")) + glob.glob(os.path.join(folder, "*.tif")) + glob.glob(os.path.join(folder, "*.tiff"))
        self.image_files.sort()

        if not self.image_files:
            self.iface.messageBar().pushMessage("Warning", "No images found.", level=1)
            return

        self.current_image_index = 0
        self.load_current_image()
        self.action_prev.setEnabled(True)
        self.action_next.setEnabled(True)

    def load_current_image(self):
        if self.current_image_index < 0 or self.current_image_index >= len(self.image_files): return
        if self.current_raster_layer: QgsProject.instance().removeMapLayer(self.current_raster_layer.id())

        image_path = self.image_files[self.current_image_index]
        image_name = os.path.basename(image_path)

        layer = QgsRasterLayer(image_path, image_name)
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer, False) 
            QgsProject.instance().layerTreeRoot().insertLayer(-1, layer) 
            self.current_raster_layer = layer
            self.canvas.setExtent(layer.extent())
            self.canvas.refresh()
            self.iface.mainWindow().statusBar().showMessage(f"Image: {image_name}")

    def next_image(self):
        if self.current_image_index < len(self.image_files) - 1:
            self.current_image_index += 1
            self.load_current_image()

    def prev_image(self):
        if self.current_image_index > 0:
            self.current_image_index -= 1
            self.load_current_image()

    # --- 3. ANNOTATION LOGIC ---
    def run(self):
        layer = self.iface.activeLayer()
        if not layer or not layer.isEditable():
            self.iface.messageBar().pushMessage("Error", "Please select an editable vector layer", level=3)
            return

        self.tool = SimplePolygonTool(self.canvas, self.iface.cadDockWidget(), self.handle_geometry)
        self.canvas.setMapTool(self.tool)

    def handle_geometry(self, geometry):
        layer = self.iface.activeLayer()
        
        standard_fields = ['Class Name', 'Remarks', 'image_name']
        for field in layer.fields():
            fname = field.name()
            if fname not in standard_fields and fname not in self.current_schema['attributes']:
                self.current_schema['attributes'][fname] = []

        dlg = AnnotationDialog(self.iface.mainWindow(), schema=self.current_schema, defaults=self.last_defaults)
        
        if dlg.exec_():
            data = dlg.get_data()
            self.last_defaults = data.copy()
            if 'Remarks' in self.last_defaults: del self.last_defaults['Remarks']

            new_label = data.get("Class Name")
            if new_label and new_label not in self.current_schema['classes']:
                self.current_schema['classes'].append(new_label)
                self.current_schema['classes'].sort()

            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            
            data['image_name'] = self.current_raster_layer.name() if self.current_raster_layer else ""

            layer_field_names = {f.name(): f.name() for f in layer.fields()}
            for field_name, value in data.items():
                if field_name in layer_field_names: feature[field_name] = value

            layer.addFeature(feature)
            self.canvas.refresh()

    # --- 4. EXPORT LOGIC (OBB Supported) ---
    def export_annotations(self):
        layer = QgsProject.instance().mapLayersByName("CVAT_Annotations")
        if not layer:
            self.iface.messageBar().pushMessage("Error", "No 'CVAT_Annotations' layer found.", level=2)
            return
        layer = layer[0]

        # 1. Open Custom Export Dialog
        dlg = ExportDialog(self.iface.mainWindow())
        if not dlg.exec_():
            return
            
        selections = dlg.get_selections()
        export_bulk = selections["is_bulk"]
        export_format = selections["format"]

        out_dir = QFileDialog.getExistingDirectory(self.iface.mainWindow(), "Select Output Directory")
        if not out_dir: return

        class_map = {name: idx for idx, name in enumerate(self.current_schema['classes'])}

        # Group polygons by image
        features_by_image = {}
        for f in layer.getFeatures():
            img_name = f['image_name']
            if not img_name: continue
            if img_name not in features_by_image:
                features_by_image[img_name] = []
            features_by_image[img_name].append(f)

        # Determine which images to process
        if export_bulk:
            images_to_process = self.image_files
        else:
            if not self.current_raster_layer:
                self.iface.messageBar().pushMessage("Error", "No active image to export.", level=2)
                return
            images_to_process = [img for img in self.image_files if os.path.basename(img) == self.current_raster_layer.name()]

        # Setup XML Root if using XML format
        xml_root = None
        if export_format == "xml":
            xml_root = ET.Element("annotations")
            meta = ET.SubElement(xml_root, "meta")
            task = ET.SubElement(meta, "task")
            labels_elem = ET.SubElement(task, "labels")
            for c_name in self.current_schema['classes']:
                label_elem = ET.SubElement(labels_elem, "label")
                name_elem = ET.SubElement(label_elem, "name")
                name_elem.text = c_name

        files_created = 0
        
        # Iterate over Images
        for img_idx, img_path in enumerate(images_to_process):
            img_name = os.path.basename(img_path)
            
            # Load raster temporarily to get extents and pixel size
            rlayer = QgsRasterLayer(img_path, "temp")
            if not rlayer.isValid(): continue
            
            ext = rlayer.extent()
            w_px = rlayer.width()
            h_px = rlayer.height()
            geo_width = ext.xMaximum() - ext.xMinimum()
            geo_height = ext.yMaximum() - ext.yMinimum()

            yolo_lines = []
            
            xml_img_elem = None
            if export_format == "xml":
                xml_img_elem = ET.SubElement(xml_root, "image", id=str(img_idx), name=img_name, width=str(w_px), height=str(h_px))

            # Process Features for this Image
            for feat in features_by_image.get(img_name, []):
                geom = feat.geometry()
                
                # --- OBB EXTRACTION MATH ---
                # orientedBoundingBox returns: (geometry, area, angle, width, height)
                obb_data = geom.orientedMinimumBoundingBox()
                obb_geom = obb_data[0] 
                
                # Extract the 4 vertices of the OBB polygon
                poly = obb_geom.asPolygon()
                if not poly: continue
                ring = poly[0] 
                points = ring[:4] # Take first 4 outer points
                
                norm_pts = []
                xml_pts = []
                
                for pt in points:
                    # Convert to normalized Image Coordinates (0 to 1)
                    nx = (pt.x() - ext.xMinimum()) / geo_width
                    ny = (ext.yMaximum() - pt.y()) / geo_height # Invert Y for image space
                    
                    # Clamp to borders
                    nx = max(0.0, min(1.0, nx))
                    ny = max(0.0, min(1.0, ny))
                    
                    # Convert to absolute pixels
                    px = nx * w_px
                    py = ny * h_px
                    
                    norm_pts.extend([nx, ny])
                    xml_pts.append(f"{px:.2f},{py:.2f}")

                # --- FORMATTING YOLO ---
                if export_format == "yolo":
                    cls_id = class_map.get(feat['Class Name'], 0)
                    # YOLOv8 OBB: class x1 y1 x2 y2 x3 y3 x4 y4
                    pt_str = " ".join([f"{val:.6f}" for val in norm_pts])
                    yolo_lines.append(f"{cls_id} {pt_str}")
                
                # --- FORMATTING XML ---
                elif export_format == "xml":
                    poly_elem = ET.SubElement(xml_img_elem, "polygon", label=feat['Class Name'], points=";".join(xml_pts))
                    
                    # Add attributes inside the polygon
                    for attr_name in self.current_schema['attributes'].keys():
                        val = feat.attribute(attr_name)
                        if val:
                            attr_elem = ET.SubElement(poly_elem, "attribute", name=attr_name)
                            attr_elem.text = str(val)

            # Save YOLO File per image
            if export_format == "yolo" and yolo_lines:
                with open(os.path.join(out_dir, os.path.splitext(img_name)[0] + ".txt"), "w") as f:
                    f.write("\n".join(yolo_lines) + "\n")
                files_created += 1

        # Finalize Output
        if export_format == "yolo":
            # Write master classes file for YOLO
            with open(os.path.join(out_dir, "classes.txt"), "w") as f:
                f.write("\n".join(self.current_schema['classes']) + "\n")
            self.iface.messageBar().pushMessage("Export Complete", f"Saved {files_created} YOLO OBB files.", level=0)
            
        elif export_format == "xml":
            # Write master XML file for CVAT
            tree = ET.ElementTree(xml_root)
            tree.write(os.path.join(out_dir, "annotations.xml"), encoding="utf-8", xml_declaration=True)
            self.iface.messageBar().pushMessage("Export Complete", "Saved CVAT annotations.xml.", level=0)