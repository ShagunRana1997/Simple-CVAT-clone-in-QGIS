from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, 
    QComboBox, QLineEdit, QDialogButtonBox, QLabel
)

class AnnotationDialog(QDialog):
    def __init__(self, parent=None, schema=None, defaults=None):
        super().__init__(parent)
        self.setWindowTitle("Annotation Details")
        self.resize(400, 500) 
        
        # Schema format: {'classes': ['Car', 'Tank'], 'attributes': {'Quality': ['1', '2'], 'Color': ['Red', 'Blue']}}
        self.schema = schema if schema else {'classes': [], 'attributes': {}}
        self.defaults = defaults if defaults else {}
        
        # We will store our dynamic widgets here so we can retrieve their values later
        self.dynamic_widgets = {}

        layout = QVBoxLayout()
        form_layout = QFormLayout()

        # --- 1. Class Name (Always Present) ---
        self.cb_class_name = QComboBox()
        self.cb_class_name.addItems(self.schema.get('classes', []))
        self.cb_class_name.setEditable(True)
        if "Class Name" in self.defaults:
            self.cb_class_name.setCurrentText(self.defaults["Class Name"])
        form_layout.addRow("Class Name:", self.cb_class_name)

        # --- 2. Dynamic Attributes (Built from CVAT XML) ---
        for attr_name, attr_values in self.schema.get('attributes', {}).items():
            if attr_values: # If it has options, make a dropdown
                widget = QComboBox()
                widget.addItems(attr_values)
                widget.setEditable(True)
                if attr_name in self.defaults:
                    widget.setCurrentText(self.defaults[attr_name])
            else: # If no options, make a text box
                widget = QLineEdit()
                if attr_name in self.defaults:
                    widget.setText(self.defaults[attr_name])
            
            form_layout.addRow(f"{attr_name}:", widget)
            self.dynamic_widgets[attr_name] = widget

        # --- 3. Remarks (Always Present) ---
        self.le_remarks = QLineEdit()
        self.le_remarks.setPlaceholderText("Enter remarks...")
        form_layout.addRow("Remarks:", self.le_remarks)

        layout.addLayout(form_layout)

        # Buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.setLayout(layout)

    def get_data(self):
        # 1. Start with the fixed fields
        data = {
            "Class Name": self.cb_class_name.currentText(),
            "Remarks": self.le_remarks.text()
        }
        
        # 2. Automatically harvest data from all dynamically generated widgets
        for attr_name, widget in self.dynamic_widgets.items():
            if isinstance(widget, QComboBox):
                data[attr_name] = widget.currentText()
            elif isinstance(widget, QLineEdit):
                data[attr_name] = widget.text()
                
        return data