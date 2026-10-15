"""Regression check for the scrolling thumbnail strip."""
import os
import shutil
import json
import zipfile
import tempfile
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSettings, QTimer, Qt, QMimeData, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QMessageBox, QTabWidget

from .backend import BookPlan, Candidate, ExportOptions, export_book, scan_folder, scan_manual_folder
from .archive_import import import_archive
from .screenshot_geometry import propose_crops
from .screenshot_import import CropChoice, stage_screenshots
from .session import clear_session
from . import diagnostics
from .gui import (ComicWindow, ImportChoiceDialog, OutputOptionsDialog, PdfImportDialog,
                  ScreenshotCropDialog, Worker, tooltip_text,
                  QueuedComic, export_queued_comic)


class ThumbnailTest(unittest.TestCase):
    original_notice = staticmethod(ComicWindow.show_startup_notice)
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        summary_patch = patch.object(ComicWindow, "screenshot_crop_summary", return_value=False)
        summary_patch.start()
        self.addCleanup(summary_patch.stop)
        notice_patch = patch.object(ComicWindow, "show_startup_notice")
        notice_patch.start()
        self.addCleanup(notice_patch.stop)
        storage_patch = patch.object(ComicWindow, "choose_batch_storage", return_value=True)
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings_file = Path(directory.name) / "settings.ini"
        settings_patch = patch("comic_builder.gui.QSettings", side_effect=lambda *_:
                               QSettings(str(settings_file), QSettings.IniFormat))
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def test_custom_prefix_scrolls_book_without_compressing_output(self):
        window = ComicWindow()
        try:
            window.pages.setCurrentWidget(window.export_page)
            window._compact_window()
            window.naming.setCurrentIndex(0)
            window.show()
            QTest.qWait(20)
            normal_size = window.size()
            control_height = window.handling.height()
            self.assertGreaterEqual(window.metadata_button.height(), window.metadata_button.sizeHint().height())
            self.assertEqual(window.book_scroll.verticalScrollBar().maximum(), 0)
            window.naming.setCurrentIndex(2)
            QTest.qWait(20)
            self.assertEqual(window.size(), normal_size)
            self.assertEqual(window.handling.height(), control_height)
            self.assertGreater(window.book_scroll.verticalScrollBar().maximum(), 0)
            self.assertGreaterEqual(window.custom_page_prefix.height(), window.book_name.height())
            window.book_scroll.ensureWidgetVisible(window.custom_page_prefix)
            QTest.qWait(20)
            window.naming.setCurrentIndex(0)
            QTest.qWait(20)
            self.assertEqual(window.book_scroll.verticalScrollBar().maximum(), 0)
        finally:
            window.close()

    def test_custom_page_naming_preview_validation_and_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(2):
                Image.new("RGB", (40, 60)).save(folder / f"{i}.png")
            window = ComicWindow()
            try:
                window.plan = BookPlan(scan_folder(folder, manual=True))
                window.book_name.setText("Superhero v01")
                window.naming.setCurrentIndex(2)
                self.assertFalse(window.custom_prefix_row.isHidden())
                self.assertFalse(window.create_button.isEnabled())
                window.custom_page_prefix.setText("Superhero v01 - 300dpi - digital")
                window.opt.format = "webp"
                window.refresh_export()
                self.assertTrue(window.create_button.isEnabled())
                self.assertIn("digital - 01.webp", window.naming.itemText(2))
                opt = window.options_from_screen()
                self.assertEqual(opt.book_name, "Superhero v01")
                self.assertEqual(opt.custom_prefix, "Superhero v01 - 300dpi - digital")
                self.assertIn("digital - 01", window.export_review_text(opt))
                window._remember_export_preferences(opt)
                second = ComicWindow()
                try:
                    self.assertEqual(second.naming.currentIndex(), 2)
                    self.assertEqual(second.custom_page_prefix.text(), opt.custom_prefix)
                    second.naming.setCurrentIndex(0)
                    self.assertTrue(second.custom_prefix_row.isHidden())
                    self.assertIsNone(second.options_from_screen().custom_prefix)
                finally:
                    second.close()
            finally:
                window.close()

    def test_settings_persist_and_workers_are_hidden_on_export(self):
        window = ComicWindow()
        try:
            def accept_settings(dialog):
                from PySide6.QtWidgets import QSpinBox, QCheckBox
                controls = dialog.findChildren(QSpinBox)
                self.assertEqual(len(controls), 3)
                for control, value in zip(controls, (2, 3, 4), strict=True):
                    control.setValue(value)
                dialog.findChildren(QCheckBox)[0].setChecked(False)
                return QDialog.Accepted
            with patch.object(QDialog, "exec", accept_settings):
                window.show_settings()
            self.assertEqual(window.settings.value("pdf_workers", type=int), 2)
            self.assertEqual(window.settings.value("screenshot_workers", type=int), 3)
            self.assertEqual(window.encoder_workers.value(), 4)
            self.assertTrue(window.encoder_workers.isHidden())
            self.assertTrue(window.settings.value("hide_source_notice", type=bool))
        finally:
            window.close()

    def test_source_notice_dismissal_is_saved(self):
        window = ComicWindow()
        try:
            def dismiss(notice):
                notice.checkBox().setChecked(True)
                return QMessageBox.Ok
            with patch.object(QMessageBox, "exec", dismiss):
                # setUp suppresses automatic startup; exercise the original method here.
                self.original_notice(window)
            with patch.object(QMessageBox, "exec") as show:
                self.original_notice(window)
                show.assert_not_called()
        finally:
            window.close()

    def test_each_copy_has_its_own_tab_and_one_is_always_made(self):
        window = ComicWindow()
        self.addCleanup(window.close)
        # Copying unmodified pages, both sizes: full-size edited pages WebP, reduced copy PNG.
        opt = ExportOptions("Sizes", keep_originals=True, size_output="both", spread_format="webp",
                            reduced_custom=True, reduced_settings={"format": "png"})
        dialog = OutputOptionsDialog(opt, window.custom_resize_values, window)
        try:
            self.assertEqual([dialog.tabs.tabText(i) for i in range(2)], ["Full size", "Reduced size"])
            full_tab, reduced_tab = dialog.tabs.widget(0), dialog.tabs.widget(1)
            # Each tab holds only its own copy's settings, under its format choice.
            self.assertTrue(full_tab.isAncestorOf(dialog.webp_quality))
            self.assertFalse(dialog.webp_quality.isHidden())
            self.assertTrue(dialog.jpeg_quality.isHidden())
            self.assertTrue(reduced_tab.isAncestorOf(dialog.reduced_format))
            self.assertTrue(reduced_tab.isAncestorOf(dialog.reduced_advanced.compress))
            self.assertFalse(full_tab.isAncestorOf(dialog.reduced_format))
            self.assertTrue(dialog.reduced_quality.isHidden())  # PNG has no quality setting.
            # Unticking a copy greys out its settings; the last one left can't be unticked.
            dialog.make_full.setChecked(False)
            self.assertFalse(dialog.full_settings.isEnabled())
            self.assertFalse(dialog.make_reduced.isEnabled())
            self.assertIn("At least one copy", dialog.make_reduced.toolTip())
            result = ExportOptions("Sizes")
            dialog.apply_to(result)
            self.assertEqual(result.size_output, "reduced")
            self.assertEqual(result.reduced_settings["format"], "png")
            dialog.make_full.setChecked(True)
            self.assertTrue(dialog.make_reduced.isEnabled())
        finally:
            dialog.reject()

    def test_reduced_copy_starts_from_the_settings_it_used_before(self):
        window = ComicWindow()
        self.addCleanup(window.close)
        # Before this change, a reduced copy without its own settings followed the main ones.
        opt = ExportOptions("Old", format="webp", webp_quality=77, size_output="both", reduced_custom=False)
        dialog = OutputOptionsDialog(opt, window.custom_resize_values, window)
        try:
            self.assertEqual(dialog.reduced_format.currentText(), "WebP")
            self.assertEqual(dialog.reduced_quality.value(), 77)
        finally:
            dialog.reject()

    def test_copy_mode_offers_webp_for_edited_pages(self):
        window = ComicWindow()
        try:
            opt = ExportOptions("WebP edits", keep_originals=True, spread_format="webp")
            dialog = OutputOptionsDialog(opt, window.custom_resize_values, window)
            self.assertEqual(dialog.spread_format.currentText(), "WebP")
            self.assertFalse(dialog.webp_quality.isHidden())
            dialog.apply_to(opt)
            self.assertEqual(opt.spread_format, "webp")
        finally:
            window.close()

    def test_calibrated_crops_proceed_without_individual_approval(self):
        from types import SimpleNamespace
        window = ComicWindow()
        try:
            proposals = [SimpleNamespace(path=Path("single.png"), kind="single", source="profile", box=(1106, 0, 3094, 3056)),
                         SimpleNamespace(path=Path("wide.png"), kind="double", source="suggested", box=(112, 0, 4088, 3056))]
            with patch.object(ScreenshotCropDialog, "exec") as review, \
                 patch.object(window, "_stage_approved_screenshots") as stage:
                window._screenshot_analysis_done(proposals)
                review.assert_not_called()
                choices = stage.call_args.args[0]
                self.assertEqual([choice.box for choice in choices], [item.box for item in proposals])
                self.assertEqual([choice.kind for choice in choices], ["single", "double"])
        finally:
            window.close()

    def test_optional_crop_preview_includes_confident_pages(self):
        from types import SimpleNamespace
        window = ComicWindow()
        try:
            window.screenshot_single_size = None
            proposals = [SimpleNamespace(path=Path("wide.png"), kind="double", source="suggested", box=(112, 0, 4088, 3056))]
            choice = CropChoice(Path("wide.png"), "double", (112, 0, 4088, 3056))
            with patch.object(window, "screenshot_crop_summary", return_value=True), \
                 patch("comic_builder.gui.ScreenshotCropDialog") as dialog, \
                 patch.object(window, "_stage_approved_screenshots") as stage:
                dialog.return_value.exec.return_value = QDialog.Accepted
                dialog.return_value.approved_choices.return_value = [choice]
                window._screenshot_analysis_done(proposals)
                self.assertEqual(dialog.call_args.args[0], proposals)
                stage.assert_called_once_with([choice])
        finally:
            window.close()

    def test_review_controls_are_compact_and_above_artwork(self):
        window = ComicWindow()
        try:
            self.assertEqual(window.back_button.size().width(), 88)
            self.assertGreaterEqual(window.back_button.size().height(), 68)
            self.assertTrue(window.remove_review_button.isHidden())
            self.assertEqual(window.review.layout().itemAt(1).layout().itemAt(1).widget(), window.back_button)
            self.assertTrue(window.remove_preview_button.isEnabled())
        finally:
            window.close()

    def test_crop_review_fits_scaled_screen_and_keeps_footer_visible(self):
        from types import SimpleNamespace
        from PySide6.QtCore import QRect
        proposal = SimpleNamespace(path=Path("not-read-during-construction.png"), canvas=(4200, 3056),
                                   kind="double", source="measured", page_width=3976,
                                   box=(112, 0, 4088, 3056), review_reason=None, family_widths=(), family_width=None)
        dialog = ScreenshotCropDialog([proposal])
        try:
            # 1920x1080 at 150%, allowing for the Windows taskbar.
            screen = Mock()
            screen.availableGeometry.return_value = QRect(0, 0, 1280, 680)
            with patch.object(dialog, "screen", return_value=screen):
                dialog.show()
                QTest.qWait(30)
                self.assertLessEqual(dialog.width(), 1240)
                self.assertLessEqual(dialog.height(), 620)
                self.assertTrue(dialog.windowFlags() & Qt.WindowMaximizeButtonHint)
                self.assertTrue(dialog.windowFlags() & Qt.WindowMinimizeButtonHint)
                self.assertTrue(dialog.isSizeGripEnabled())
                for expanded in (False, True):
                    dialog.guide_edit_button.setChecked(expanded)
                    QTest.qWait(30)
                    for button in dialog.crop_footer_buttons:
                        rect = button.rect().translated(button.mapTo(dialog, button.rect().topLeft()))
                        self.assertTrue(dialog.rect().contains(rect), button.text())
        finally:
            dialog.reject()

    def test_crop_dialog_defers_decoding_and_closes_adjustment_tools(self):
        from types import SimpleNamespace
        window = ComicWindow()
        dialog = None
        try:
            proposal = SimpleNamespace(path=Path("not-read-during-construction.png"), canvas=(4200, 3056),
                                       kind="double", source="measured", page_width=3976,
                                       box=(112, 0, 4088, 3056), review_reason=None, family_widths=(), family_width=None)
            with patch("comic_builder.gui.Image.open", side_effect=AssertionError("GUI decoded an image")):
                dialog = ScreenshotCropDialog([proposal])
                dialog.guide_edit_button.setChecked(True)
                dialog.crop_left.setValue(113)
                dialog.adjust_crop()
                self.assertFalse(dialog.guide_edit_button.isChecked())
                self.assertTrue(dialog.manual_widget.isHidden())
                self.assertEqual(dialog.choices[0].box[0], 113)
                dialog.guide_edit_button.setChecked(True)
                dialog.crop_left.setValue(120)
                dialog.guide_edit_button.setChecked(False)
                self.assertEqual(dialog.choices[0].box[0], 113)
                dialog._return_to_summary()
                self.assertTrue(dialog.back_to_summary)
        finally:
            if dialog is not None:
                dialog.close()
            window.close()

    def test_explorer_launch_passes_paths_to_import_flow(self):
        from .gui import main
        with patch("comic_builder.gui.QApplication") as application, \
             patch("comic_builder.gui.ComicWindow") as window, \
             patch("comic_builder.gui.LaunchBroker") as broker, \
             patch("comic_builder.gui.sys.argv", ["ComicStitcher.exe", "C:/My Comics/Book.cbz"]):
            broker.return_value.start.return_value = True
            application.return_value.exec.return_value = 0
            self.assertEqual(main(), 0)
            broker.return_value.start.assert_called_once_with([Path("C:/My Comics/Book.cbz")])
            broker.return_value.paths_received.connect.assert_called_once_with(window.return_value._external_import)

    def test_output_order_option_is_only_for_reversed_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            Image.new('RGB', (80, 120), 'red').save(folder / '001.png')
            window = ComicWindow()
            try:
                window.scan_done(scan_folder(folder, manual=True, right_to_left=True))
                window.refresh_export()
                self.assertFalse(window.front_to_back.isHidden())
                window.front_to_back.setChecked(False)
                self.assertFalse(window.options_from_screen().front_to_back)
                window.plan.scan.right_to_left = False
                window.refresh_export()
                self.assertTrue(window.front_to_back.isHidden())
                self.assertTrue(window.options_from_screen().front_to_back)
            finally:
                window.close()

    def test_pdf_render_preview_starts_at_front_cover_for_reversed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'book.pdf'
            Image.new('RGB', (80, 120), 'red').save(
                source, 'PDF', save_all=True, append_images=[Image.new('RGB', (100, 140), 'blue')])
            dialog = PdfImportDialog(source, 200, 0, right_to_left=True)
            try:
                self.assertTrue(dialog.jpeg_reason.isHidden())  # No reason given: no line.
                deadline = time.monotonic() + 3
                while dialog.current_size is None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertEqual(dialog.current_size, (100, 140))
                dialog.page.setValue(2)
                deadline = time.monotonic() + 3
                while dialog.current_size is None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertEqual(dialog.current_size, (80, 120))
            finally:
                dialog.reject()

    def test_manga_checkbox_defaults_off_for_each_input_type(self):
        for name in ('pages', 'book.pdf', 'book.cbz', 'book.cbr', 'book.cb7'):
            dialog = ImportChoiceDialog(Path(name), folder=name == 'pages')
            try:
                self.assertFalse(dialog.right_to_left.isChecked())
            finally:
                dialog.close()

    def test_rtl_preview_pairs_and_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            Image.new('RGB', (80, 120), 'red').save(folder / '001.png')
            Image.new('RGB', (80, 120), 'blue').save(folder / '002.png')
            window = ComicWindow()
            try:
                window.scan_done(scan_folder(folder, manual=True, right_to_left=True))
                with patch.object(window.review_worker, 'request') as request:
                    window.show_candidate()
                    self.assertEqual(request.call_args.args[1:], (folder / '001.png', folder / '002.png'))
                window.plan.decide(False)
                window.enter_preview()
                self.app.processEvents()
                window.comparing = (0, 1)
                window.show_preview()
                self.assertEqual(window.preview_pair.left.toImage().pixelColor(0, 0).name(), '#ff0000')
                self.assertEqual(window.preview_pair.right.toImage().pixelColor(0, 0).name(), '#0000ff')
                with patch.object(window, 'navigate') as navigate:
                    window._review_key('left')
                    navigate.assert_called_once_with(1)
                window.show()
                self.app.processEvents()
                window._resize_thumbnail_strip()
                self.assertGreater(window.thumb_buttons[0].x(), window.thumb_buttons[1].x())
            finally:
                window.close()

    def test_explorer_selection_waits_during_review(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "next.cbz"
            source.touch()
            window = ComicWindow()
            try:
                current_plan = object()
                window.plan = current_plan
                with patch.object(window, "refresh_export"), patch.object(window, "_prepare_import") as prepare:
                    window._external_import([source])
                self.assertIs(window.plan, current_plan)
                self.assertEqual(list(window.pending_imports), [source])
                self.assertTrue(window.batch_import_active)
                prepare.assert_not_called()
            finally:
                window.plan = None
                window.close()

    def test_about_opens_bundled_license_index_and_folder_offline(self):
        import sys
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QPushButton
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            licenses = root / 'licenses'
            licenses.mkdir()
            index = licenses / 'index.html'
            index.write_text('<h1>Notices</h1>')
            window = ComicWindow()
            def inspect(dialog):
                dialog.findChild(QPushButton, 'view_license_notices').click()
                dialog.findChild(QPushButton, 'open_license_folder').click()
                return QDialog.Accepted
            try:
                with patch.object(sys, 'frozen', True, create=True), \
                     patch.object(sys, 'executable', str(root / 'ComicStitcher.exe')), \
                     patch.object(QDialog, 'exec', inspect), \
                     patch.object(QDesktopServices, 'openUrl', return_value=True) as opener:
                    window.show_about()
                # Qt writes local paths with forward slashes on every platform.
                self.assertEqual([Path(call.args[0].toLocalFile()) for call in opener.call_args_list],
                                 [index, licenses])
            finally:
                window.close()

    def test_icon_size_respects_display_scale_and_page_shapes_share_height(self):
        from .gui import review_control
        from PySide6.QtWidgets import QPushButton
        controls = []
        for name in ('No', 'Seam', 'Remove', 'Single', 'Double', 'Sideways', 'Swap', 'Replace'):
            button = QPushButton()
            review_control(button, '?', name)
            button.show()
            self.app.processEvents()
            pix = button.review_icon.pixmap()
            self.assertFalse(pix.isNull(), name)
            size = pix.deviceIndependentSize()
            self.assertLessEqual(size.height(), 32.1, name)
            self.assertLessEqual(size.width(), 44.1 if name in ('Single', 'Double', 'Sideways') else 32.1, name)
            self.assertLessEqual(size.height(), button.review_icon.contentsRect().height(), name)
            controls.append(button)
        self.assertAlmostEqual(controls[3].review_icon.pixmap().deviceIndependentSize().height(),
                               controls[4].review_icon.pixmap().deviceIndependentSize().height(), delta=1)
        for button in controls:
            button.close()

    def test_pair_review_seam_has_yellow_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (120, 160), "red").save(folder / "001.png")
            Image.new("RGB", (120, 160), "blue").save(folder / "002.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                from .gui import interface_icon
                from PySide6.QtCore import QSize
                self.assertEqual(window.seam_button.review_icon.pixmap().toImage(),
                                 interface_icon('seam-view').pixmap(QSize(32, 32)).toImage())
                window.toggle_seam()
                self.assertEqual(window.seam_button.review_icon.pixmap().toImage(),
                                 interface_icon('seam-view', zoomed=True).pixmap(QSize(32, 32)).toImage())
                image = window.seam_image.pixmap().toImage()
                self.assertEqual(image.pixelColor(image.width() // 2, 20).name(), "#ffe500")
                window.toggle_seam()
                self.assertEqual(window.seam_button.review_icon.pixmap().toImage(),
                                 interface_icon('seam-view').pixmap(QSize(32, 32)).toImage())
            finally:
                window.close()

    def test_final_seam_marker_is_preview_only(self):
        with tempfile.TemporaryDirectory() as directory:
            left, right = Path(directory) / "left.png", Path(directory) / "right.png"
            Image.new("RGB", (120, 160), "red").save(left)
            Image.new("RGB", (120, 160), "blue").save(right)
            original = left.read_bytes(), right.read_bytes()
            window = ComicWindow()
            try:
                window._show_preview_seam(left, right)
                image = window.preview_seam_image.pixmap().toImage()
                self.assertEqual(image.pixelColor(image.width() // 2, 20).name(), "#ffe500")
                self.assertEqual(window.preview_view.currentIndex(), 1)
                window._close_preview_seam()
                self.assertEqual(window.preview_view.currentIndex(), 0)
                self.assertEqual((left.read_bytes(), right.read_bytes()), original)
            finally:
                window.close()

    def test_final_seam_space_overrides_focused_edit_button(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, color in (("001.png", "red"), ("002.png", "blue")):
                Image.new("RGB", (300, 2000), color).save(folder / name)
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                window.enter_preview()
                self.app.processEvents()
                window.comparing = (0, 1)
                window.show_preview()
                before = window.plan.entries()
                window.activateWindow()
                window.join_button.setFocus()
                self.app.processEvents()
                QTest.keyClick(window.join_button, Qt.Key_Space)
                self.assertTrue(window.preview_seam_active)
                self.assertEqual(window.plan.entries(), before)
                self.assertEqual(window.preview_view.currentIndex(), 1)
                self.app.processEvents()
                QTest.keyClick(window.preview_seam_area, Qt.Key_Down)
                self.assertGreater(window.preview_seam_area.verticalScrollBar().value(), 0)
                QTest.keyClick(window.preview_seam_area, Qt.Key_Space)
                self.assertFalse(window.preview_seam_active)
                self.assertEqual(window.comparing, (0, 1))
                window.preview_seam_button.click()
                self.assertTrue(window.preview_seam_active)
                QTest.keyClick(window.preview_seam_area, Qt.Key_Escape)
                self.assertFalse(window.preview_seam_active)
                window.preview_seam()
                window.navigate(1)
                self.assertFalse(window.preview_seam_active)
                self.assertEqual(window.plan.entries(), before)
            finally:
                window.close()

    def test_folder_import_destination_is_beside_source_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Book Pages"
            source.mkdir()
            Image.new("RGB", (40, 60), "white").save(source / "001.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(source))
                self.assertEqual(window.plan.scan.output_folder, source.parent)
                scan = scan_manual_folder(source)
                scan.screenshot_source = source
                scan.output_folder = source
                window.scan_done(scan)
                self.assertEqual(window.plan.scan.output_folder, source.parent)
            finally:
                window.close()

    def test_output_formats_control_advanced_panels_and_reduced_override(self):
        window = ComicWindow()
        dialog = OutputOptionsDialog(ExportOptions("Test"), window.custom_resize_values)
        try:
            self.assertTrue(dialog.advanced.format.isHidden())
            dialog.format.setCurrentText("WebP")
            self.assertEqual(dialog.advanced.stack.currentIndex(), 1)
            dialog.make_reduced.setChecked(True)
            dialog.reduced_format.setCurrentText("JPEG")
            dialog.reduced_quality.setValue(61)
            self.assertEqual(dialog.reduced_advanced.stack.currentIndex(), 0)
            result = ExportOptions("Test")
            dialog.apply_to(result)
            self.assertEqual(result.size_output, "both")
            self.assertTrue(result.reduced_custom)
            self.assertEqual(result.format, "webp")
            self.assertEqual(result.reduced_settings["format"], "jpeg")
            self.assertEqual(result.reduced_settings["jpeg_quality"], 61)
        finally:
            dialog.close()
            window.close()

    def test_crop_family_selector_updates_guide(self):
        from .screenshot_geometry import CropProposal
        proposal = CropProposal(Path("pending.png"), (4200, 3056), "uncertain", None,
                                (300, 0, 3900, 3056), "review",
                                family_widths=(1988, 2037))
        dialog = ScreenshotCropDialog([proposal])
        try:
            self.assertEqual(dialog._guide("single", 0)[2] - dialog._guide("single", 0)[0], 1988)
            dialog.family_combo.setCurrentIndex(1)
            self.assertEqual(dialog._guide("single", 0)[2] - dialog._guide("single", 0)[0], 2037)
            dialog.choose("single")
            self.assertEqual(dialog.choices[0].box[2] - dialog.choices[0].box[0], 2037)
        finally:
            dialog.close()

    def test_crop_strip_centers_current_thumbnail(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.png"
            Image.new("RGB", (420, 306), "white").save(path)
            proposal = SimpleNamespace(path=path, canvas=(420, 306), kind="double", source="measured",
                                       page_width=396, box=(12, 0, 408, 306), review_reason=None, family_widths=(), family_width=None)
            dialog = ScreenshotCropDialog([proposal] * 30)
            try:
                dialog.resize(800, 600)
                dialog.show()
                self.app.processEvents()
                for index in (0, 15, 29):
                    dialog.list.setCurrentRow(index)
                    self.app.processEvents()
                    dialog._center_crop_thumbnail()
                    rect = dialog.list.visualItemRect(dialog.list.item(index))
                    self.assertLess(abs(rect.center().x() - dialog.list.viewport().width() // 2), 5)
                from PySide6.QtWidgets import QPushButton
                self.assertFalse(any(button.text() == "Never mind" for button in dialog.findChildren(QPushButton)))
            finally:
                dialog.close()

    def wait_for(self, predicate, seconds=3):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), "GUI did not reach the expected state")

    def test_manual_import_reviews_every_neighbor_with_touching_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(4):
                Image.new("RGB", (70, 95), "black").save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.review_mode.setCurrentIndex(1)
                with patch("comic_builder.backend.classify_page",
                           side_effect=AssertionError("manual mode ran the detector")):
                    window.start_scan(folder)
                    self.wait_for(lambda: window.plan is not None)
                    self.wait_for(lambda: not window.worker.isRunning())
                self.assertTrue(window.plan.scan.manual_mode)
                self.assertIn("Manual", window.review_heading.text())
                self.wait_for(lambda: window.joined_review.left is not None)
                left, right = window.joined_review.image_rects()
                self.assertAlmostEqual(left.right(), right.left())
                window.decide(False)
                self.assertEqual(window.plan.current()[1].left, 1)
                window.review_back()
                self.assertEqual(window.plan.current()[1].left, 0)
                window.decide(True)
                self.assertEqual(window.plan.current()[1].left, 2)
                window.decide(False)
                self.wait_for(lambda: window.pages.currentWidget() == window.preview)
                self.assertEqual(window.plan.entries(), [(0, 1), (2, None), (3, None)])
            finally:
                window.close()

    def test_folder_import_confirmation_can_override_screenshot_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (420, 306), "black").save(folder / "001.png")
            window = ComicWindow()
            try:
                self.assertTrue(window.import_zone.isVisibleTo(window))
                self.assertTrue(window.import_zone.files_button.isVisibleTo(window))
                self.assertTrue(window.import_zone.folder_button.isVisibleTo(window))
                self.assertFalse(window.review_mode.isVisibleTo(window))
                def choose_manual_pages(dialog):
                    self.assertEqual(dialog.folder_type.currentIndex(), 1)
                    self.assertFalse(any("will not be changed" in label.text() for label in dialog.findChildren(QLabel)))
                    dialog.folder_type.setCurrentIndex(0)
                    dialog.review_mode.setCurrentIndex(1)
                    return QDialog.Accepted
                with patch("comic_builder.gui.looks_like_screenshot_folder", return_value=(True, 1)), \
                     patch.object(ImportChoiceDialog, "exec", choose_manual_pages), \
                     patch.object(window, "start_scan") as start:
                    window._prepare_import(folder)
                start.assert_called_once_with(folder.resolve())
                self.assertFalse(window.screenshot_mode.isChecked())
                self.assertEqual(window.review_mode.currentIndex(), 1)
            finally:
                window.close()

    def test_review_removal_can_be_undone_with_back(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(4):
                Image.new("RGB", (70, 95), "white").save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                def choose_right(dialog):
                    next(button for button in dialog.buttons()
                         if button.text().startswith("Right:")).click()
                    return dialog.result()
                with patch.object(QMessageBox, "exec", choose_right), \
                     patch.object(window, "_confirm_page_removal", return_value=True):
                    window.remove_review_page()
                self.assertEqual(window.plan.removed_pages, {1})
                self.assertEqual((window.plan.current()[1].left, window.plan.current()[1].right), (2, 3))
                window.review_back()
                self.assertEqual(window.plan.removed_pages, set())
                self.assertEqual((window.plan.current()[1].left, window.plan.current()[1].right), (0, 1))
            finally:
                window.close()

    def test_preview_can_remove_unoffered_spacer_and_undo_it(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(3):
                Image.new("RGB", (70, 95), "black").save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_folder(folder))
                self.wait_for(lambda: window.pages.currentWidget() == window.preview and
                              len(getattr(window, "thumb_buttons", [])) == 3)
                window.preview_index = 1
                window.show_preview()
                with patch.object(window, "_confirm_page_removal", return_value=True):
                    window.remove_preview_page()
                self.assertEqual(window.plan.removed_pages, {1})
                self.assertEqual(len(window.thumb_buttons), 2)
                self.assertTrue(window.undo_remove_button.isEnabled())
                window.undo_preview_removal()
                self.assertEqual(window.plan.removed_pages, set())
                self.assertEqual(len(window.thumb_buttons), 3)
            finally:
                window.close()

    def test_file_confirmation_names_the_actual_input_format(self):
        for extension, label in (("pdf", "PDF"), ("cbz", "CBZ"), ("cb7", "CB7"),
                                 ("cbr", "CBR"), ("zip", "ZIP"), ("7z", "7z"),
                                 ("rar", "RAR")):
            with self.subTest(extension=extension):
                dialog = ImportChoiceDialog(Path(f"book.{extension}"), folder=False)
                self.assertIn(label, dialog.file_description.text())
                self.assertIsNone(dialog.pdf_smooth_images)
                dialog.close()

    def test_comicvine_lookup_search_browse_preview_and_field_selection(self):
        from .comicvine import ComicVineClient
        from .comicvine_dialog import ComicVineDialog
        from .metadata_dialog import MetadataDialog
        from .test_comicvine import ISSUE, VOLUME
        def transport(url, *args):
            if '/search/' in url: result = [dict(VOLUME, resource_type='volume')]
            elif '/issues/' in url: result = [ISSUE]
            elif '/issue/' in url: result = ISSUE
            else: result = VOLUME
            return json.dumps({'status_code': 1, 'results': result, 'number_of_total_results': 1}).encode()
        client = ComicVineClient('test-only-key', transport=transport, interval=0)
        dialog = ComicVineDialog(client, 'Current comic')
        editor = MetadataDialog('<ComicInfo><Title>Keep title</Title><Writer>Existing writer</Writer><Custom>Keep this</Custom></ComicInfo>')
        try:
            dialog.show(); self.assertEqual(dialog.query.text(), 'Current comic')
            dialog.search(); self.wait_for(lambda: not dialog.busy)
            self.assertEqual(dialog.results.count(), 1)
            self.assertFalse(dialog.use.isEnabled())
            dialog.inspect_selected(); self.wait_for(lambda: not dialog.busy)
            self.assertEqual(dialog.mode, 'issues')
            dialog.inspect_selected(); self.wait_for(lambda: not dialog.busy)
            self.assertTrue(dialog.use.isEnabled())
            self.assertIn('Publisher', dialog.preview.toPlainText())
            dialog.use_fields()
            self.assertEqual(dialog.result(), QDialog.Accepted)
            editor.controls['Writer'][0].setChecked(False)
            editor.fill_from_lookup(dialog.result_fields)
            self.assertEqual(editor.controls['Title'][1].text(), 'Keep title')
            self.assertFalse(editor.controls['Writer'][0].isChecked())
            self.assertTrue(editor.controls['Publisher'][0].isChecked())
            editor.fill_from_lookup(dialog.result_fields, replace_existing=True)
            self.assertEqual(editor.controls['Title'][1].text(), 'Book & title')
            self.assertTrue(editor.controls['Writer'][0].isChecked())
            editor.controls['Summary'][0].setChecked(False)
            editor.apply()
            self.assertIn('<Custom>Keep this</Custom>', editor.result_xml)
            self.assertNotIn('<Summary>', editor.result_xml)
            self.assertNotIn('test-only-key', editor.result_xml)
        finally:
            dialog.reject(); editor.reject()

    def test_comicvine_lookup_cancel_does_not_wait_for_network(self):
        import threading
        from .comicvine import ComicVineClient
        from .comicvine_dialog import ComicVineDialog
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        def transport(*args):
            entered.set()
            release.wait(3)
            completed.set()
            return b'{"status_code":1,"results":[],"number_of_total_results":0}'
        dialog = ComicVineDialog(ComicVineClient('test-only-key', transport=transport, interval=0), 'Comic')
        try:
            dialog.show(); dialog.search()
            self.wait_for(entered.is_set)
            started = time.monotonic(); dialog.reject()
            self.assertLess(time.monotonic() - started, .5)
            self.assertTrue(dialog.cancel_event.is_set())
            self.assertEqual(dialog.result(), QDialog.Rejected)
            release.set(); self.wait_for(completed.is_set)
            self.app.processEvents()
            self.assertEqual(dialog.result_fields, {})
        finally:
            release.set(); dialog.reject()

    def test_comicvine_key_is_optional_and_remembered_value_is_protected(self):
        from PySide6.QtWidgets import QLineEdit, QCheckBox
        window = ComicWindow()
        try:
            def accept_settings(dialog):
                key = dialog.findChild(QLineEdit, 'comicvine_api_key')
                self.assertEqual(key.echoMode(), QLineEdit.Password)
                key.setText('test-only-key')
                dialog.findChild(QCheckBox, 'remember_comicvine_key').setChecked(True)
                return QDialog.Accepted
            with patch('comic_builder.gui.can_remember_key', return_value=True), \
                 patch('comic_builder.gui.protect_key', return_value='protected-test-value'), \
                 patch.object(QDialog, 'exec', accept_settings):
                window.show_settings()
            self.assertTrue(window.comicvine_client.configured)
            self.assertEqual(window.settings.value('comicvine_key_protected'), 'protected-test-value')
            self.assertNotIn('test-only-key', ' '.join(str(window.settings.value(k)) for k in window.settings.allKeys()))
            def clear_settings(dialog):
                dialog.findChild(QLineEdit, 'comicvine_api_key').clear()
                return QDialog.Accepted
            with patch.object(QDialog, 'exec', clear_settings): window.show_settings()
            self.assertFalse(window.comicvine_client.configured)
            self.assertFalse(window.settings.contains('comicvine_key_protected'))
        finally:
            window.close()

    def test_metadata_editor_preserves_other_fields_and_is_per_comic(self):
        from .metadata_dialog import MetadataDialog
        from .comicinfo import parse_xml
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'first'; folder.mkdir()
            Image.new('RGB', (80, 120), 'white').save(folder / '001.png')
            (folder / 'ComicInfo.xml').write_text('<ComicInfo><Title>Existing</Title><ScanInformation>Keep</ScanInformation><Custom>Also keep</Custom></ComicInfo>')
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_folder(folder, manual=True))
                window.show_export()
                self.assertFalse(window.include_comicinfo.isChecked())
                window.include_comicinfo.setChecked(True)
                def edit(dialog):
                    check, control = dialog.controls['Publisher']
                    check.setChecked(True); control.setText('Example publisher')
                    dialog.apply()
                    return dialog.result()
                with patch.object(MetadataDialog, 'exec', edit): window.show_metadata()
                root = parse_xml(window.options_from_screen().comicinfo_xml)
                self.assertEqual(root.findtext('Publisher'), 'Example publisher')
                self.assertEqual(root.findtext('Custom'), 'Also keep')
                self.assertEqual(root.findtext('ScanInformation'), 'Keep')
                window.include_comicinfo.setChecked(False)
                self.assertIsNone(window.options_from_screen().comicinfo_xml)
                self.assertFalse(window.metadata_button.isEnabled())
                other = Path(tmp) / 'second'; other.mkdir()
                Image.new('RGB', (80, 120), 'white').save(other / '001.png')
                window.scan_done(scan_folder(other, manual=True))
                self.assertFalse(window.include_comicinfo.isChecked())
                self.assertIsNone(window.plan.comicinfo_xml)
            finally:
                window.close()

    def test_cancelling_pdf_import_restores_launch_window_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'comic.pdf'
            Image.new('RGB', (80, 120), 'red').save(source, 'PDF')
            for cancel_at in ('extraction', 'render'):
                with self.subTest(cancel_at=cancel_at):
                    window = ComicWindow()
                    try:
                        window.show()
                        def choose(dialog, cancel_at=cancel_at):
                            if cancel_at == 'extraction':
                                dialog.button(QMessageBox.Cancel).click()
                            else:
                                next(b for b in dialog.buttons() if b.text() == 'Render to PNG instead').click()
                            return dialog.result()
                        def cancel_render(dialog):
                            dialog.reject()
                            return QDialog.Rejected
                        with patch.object(QMessageBox, 'exec', choose), \
                             patch.object(PdfImportDialog, 'exec', cancel_render):
                            window.start_scan(source, manual_mode=True)
                            self.wait_for(lambda window=window: window.pages.currentIndex() == 0)
                        self.assertEqual(window.size(), window.compact_size)
                        self.assertIsNone(window.plan)
                    finally:
                        window.close()

    def test_eligible_pdf_extracts_without_render_dialog_and_reimports_for_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'comic.pdf'
            Image.new('RGB', (80, 120), 'red').save(source, 'PDF', save_all=True,
                append_images=[Image.new('RGB', (80, 120), 'blue')])
            window = ComicWindow()
            try:
                def extract(dialog):
                    next(button for button in dialog.buttons()
                         if button.text() == 'Extract original JPEGs').click()
                    return dialog.result()
                with patch.object(QMessageBox, 'exec', extract), \
                     patch.object(PdfImportDialog, 'exec', side_effect=AssertionError('Rendered')):
                    window.start_scan(source, manual_mode=True)
                    self.wait_for(lambda: window.plan is not None)
                self.assertTrue(window.pdf_session_options['extract_jpegs'])
                self.assertTrue(all(page.path.suffix == '.jpg' for page in window.plan.scan.pages))
                stat = source.stat()
                job = QueuedComic(window.plan, ExportOptions('Queued JPEGs', format='png', workers=1),
                                  source, None, None, reimport_source=source,
                                  source_stat=(stat.st_size, stat.st_mtime_ns),
                                  pdf_options=dict(window.pdf_session_options))
                result = export_queued_comic(job)
                self.assertTrue(result.is_file())
            finally:
                window.close()

    def test_pdf_confirmation_passes_smoothing_choice_to_render_dialog(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "comic.pdf"
            Image.new("RGB", (72, 100), "red").save(source, "PDF", resolution=72)
            window = ComicWindow()
            window.settings.setValue("pdf_smooth_images", False)
            try:
                def choose_unsmoothed(dialog):
                    self.assertIsNone(dialog.pdf_smooth_images)
                    return QDialog.Accepted
                def check_render_settings(dialog):
                    self.assertFalse(dialog.smooth_images.isChecked())
                    # The window says why the original JPEGs weren't offered.
                    self.assertFalse(dialog.jpeg_reason.isHidden())
                    self.assertIn("Page 1: the JPEG is CMYK.", dialog.jpeg_reason.text())
                    dialog.reject()
                    return dialog.result()
                with patch.object(ImportChoiceDialog, "exec", choose_unsmoothed), \
                     patch.object(PdfImportDialog, "exec", check_render_settings), \
                     patch("comic_builder.gui.check_pdf_jpegs", return_value=(None, "Page 1: the JPEG is CMYK.")):
                    window._prepare_import(source)
                    self.wait_for(lambda window=window: window.pages.currentIndex() == 0)
            finally:
                window.close()

    def test_progress_window_can_shrink_below_review_and_export_sizes(self):
        window = ComicWindow()
        try:
            window.show()
            window.pages.setCurrentWidget(window.progress_page)
            window.progress_label.setText("Rendering PDF pages: 136 of 212…")
            window._compact_window()
            self.app.processEvents()
            # 580 wide, unless one line of progress text needs more with this system's
            # fonts (the screen-less test font on Windows is unusually wide).
            self.assertLessEqual(window.width(), max(600, window._progress_minimum().width()))
            self.assertLessEqual(window.height(), 260)
            window.pages.setCurrentIndex(0)
            window._compact_window()
            self.app.processEvents()
            self.assertGreaterEqual(window.height(), 500)
        finally:
            window.close()

    @unittest.skipIf(sys.platform == "win32", "Windows applies window sizes immediately")
    def test_compact_size_survives_a_window_manager_restoring_the_old_size(self):
        window = ComicWindow()
        try:
            window.show()
            window.pages.setCurrentWidget(window.progress_page)
            window._compact_window()
            # Simulate a Linux window manager finishing "un-maximize" late and
            # putting back the earlier, larger size.
            window.resize(1100, 800)
            deadline = time.monotonic() + 1.5
            while window.height() > 260 and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertLessEqual(window.width(), 600)
            self.assertLessEqual(window.height(), 260)
        finally:
            window.close()

    def test_create_comic_screen_grows_to_fit_wrapped_text(self):
        window = ComicWindow()
        try:
            window.show()
            window.pages.setCurrentWidget(window.export_page)
            screen = window.screen() or QApplication.primaryScreen()
            room = screen.availableGeometry().height()
            if room <= 600:
                self.skipTest("The test screen is too short to show the difference")
            # Pretend larger fonts make the content need 600 px at this width.
            with patch.object(window.export_page, "hasHeightForWidth", return_value=True), \
                 patch.object(window.export_page, "heightForWidth", return_value=600):
                window._compact_window()
                self.app.processEvents()
            self.assertGreaterEqual(window.height(), 600)
            self.assertLessEqual(window.height(), room)
        finally:
            window.close()

    def test_drop_returns_before_import_dialogs_open(self):
        window = ComicWindow()
        try:
            for target in (window, window.import_zone):
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile("/tmp/example.pdf")])
                event = Mock()
                event.mimeData.return_value = mime
                with patch.object(window, "_import_paths") as importer:
                    target.dropEvent(event)
                    event.acceptProposedAction.assert_called_once()
                    importer.assert_not_called()
                    self.wait_for(lambda: importer.called)
                    importer.assert_called_once_with([Path("/tmp/example.pdf")])
        finally:
            window.close()

    def test_multiple_files_are_offered_one_at_a_time(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [Path(directory) / f"comic-{i}.pdf" for i in range(3)]
            for source in sources:
                source.touch()
            window = ComicWindow()
            try:
                with patch.object(window, "_prepare_import") as prepare:
                    window._import_paths(sources)
                    prepare.assert_called_once_with(sources[0].resolve())
                    self.assertEqual(list(window.pending_imports), sources[1:])
                    window._open_next_pending()
                    self.assertEqual(prepare.call_args.args[0], sources[1].resolve())
            finally:
                window.close()

    def test_batch_storage_choice_precedes_import_and_cancel_stops_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = [Path(directory) / f"comic-{i}.pdf" for i in range(2)]
            for source in sources:
                source.touch()
            window = ComicWindow()
            try:
                calls = []
                with patch.object(window, "choose_batch_storage", side_effect=lambda: calls.append("storage") or True), \
                     patch.object(window, "_prepare_import", side_effect=lambda source: calls.append("import")):
                    window._import_paths(sources)
                self.assertEqual(calls, ["storage", "import"])
                window.pending_imports.clear()
                with patch.object(window, "choose_batch_storage", return_value=False), \
                     patch.object(window, "_prepare_import") as prepare:
                    window._import_paths(sources)
                    prepare.assert_not_called()
                    self.assertFalse(window.pending_imports)
            finally:
                window.close()

    def test_batch_export_has_one_action_and_last_book_starts_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (60, 80), "white").save(folder / "001.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                window.batch_import_active = True
                window.pending_imports.append(folder / "next.pdf")
                window.show_export()
                self.assertTrue(window.create_button.isHidden())
                self.assertEqual(window.add_queue_button.text(), "Review final output")
                window.pending_imports.clear()
                window.refresh_export()
                self.assertEqual(window.add_queue_button.text(), "Review final output")
                with patch.object(window, "confirm_export", return_value=True), \
                     patch.object(window, "start_queue") as start:
                    window.add_to_queue()
                    self.wait_for(lambda: start.called)
                    self.assertEqual(len(window.queue_jobs), 1)
                    self.assertFalse(window.batch_import_active)
                window.refresh_export()
                self.assertFalse(window.create_button.isHidden())
                window.queue_jobs.clear()
            finally:
                window.close()

    def test_adding_reviewed_comic_opens_next_selected_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "pages"
            folder.mkdir()
            Image.new("RGB", (60, 80), "white").save(folder / "001.png")
            next_source = root / "next.pdf"
            next_source.touch()
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_manual_folder(folder))
                window.show_export()
                window.book_name.setText("First Queued")
                window.pending_imports.append(next_source)
                with patch.object(window, "confirm_export", return_value=True), \
                     patch.object(window, "_prepare_import") as prepare:
                    window.add_to_queue()
                    self.wait_for(lambda: prepare.called)
                prepare.assert_called_once_with(next_source)
                self.assertEqual(len(window.queue_jobs), 1)
            finally:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.close()

    def test_return_review_after_move_keeps_unchanged_rejections(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for i in range(4): Image.new("RGB", (40, 60), "white").save(root / f"{i:03}.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(root))
                for _ in range(3): window.plan.decide(False)
                window.plan.move_entry(0, 1)
                window.enter_preview()
                with patch.object(window, "show_candidate"):
                    window.return_review()
                self.assertEqual(window.plan.decisions, {0: False, 1: False, 2: False})
                self.assertEqual((window.plan.current()[1].left, window.plan.current()[1].right), (1, 0))
            finally: window.close()

    def test_reordered_queue_reimport_preserves_new_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); pages = root / "pages"; pages.mkdir()
            for i in range(4): Image.new("RGB", (40, 60), "white").save(pages / f"{i:03}.png")
            original = scan_manual_folder(pages); plan = BookPlan(original)
            for _ in range(3): plan.decide(False)
            plan.move_entry(0, 1); plan.decide(False); plan.decide(True)
            source = root / "book.pdf"; source.write_bytes(b"source identity")
            stat = source.stat()
            job = QueuedComic(plan, ExportOptions("Test", workers=1), source, None, None, source,
                              (stat.st_size, stat.st_mtime_ns), {"dpi": 200})
            with patch("comic_builder.gui.import_pdf", return_value=scan_manual_folder(pages)), patch(
                    "comic_builder.gui.export_book") as exporter:
                export_queued_comic(job)
                restored = exporter.call_args.args[0]
                self.assertEqual(restored.entries(), [(1, None), (0, 2), (3, None)])
                restored.validate_entries()

    def test_cleanup_failure_does_not_hide_success_or_stop_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (40, 60), "white").save(root / "001.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(root))
                window.show_export()
                window.opt = ExportOptions("Finished", workers=1)
                window.session_source = root
                destination = root / "Finished.cbz"
                with patch("comic_builder.gui.clear_session", side_effect=PermissionError("locked")), patch(
                        "comic_builder.gui.diagnostics.exception") as logged:
                    window.export_done(destination)
                    self.assertFalse(window.done_button.isHidden())
                    self.assertFalse(window.repeat_button.isHidden())
                    self.assertIn("Finished.cbz", window.progress_label.text())
                    logged.assert_called_once()
                    job = Mock(plan=window.plan, options=window.opt, session_source=root,
                               import_work=Mock(), screenshot_stage_dir=root / "staged")
                    job.import_work.cleanup.side_effect = PermissionError("locked temp folder")
                    window.queue_jobs = [job]
                    window.active_queue_job = job
                    with patch("comic_builder.gui.shutil.rmtree", side_effect=PermissionError("locked crops")), patch(
                            "comic_builder.gui.QTimer.singleShot") as advance, patch(
                            "comic_builder.gui.queued_outputs", return_value=[destination]):
                        window._queued_export_done(destination)
                        self.assertEqual(window.queue_completed, 1)
                        self.assertFalse(window.queue_jobs)
                        self.assertIsNone(window.active_queue_job)
                        self.assertIn(destination, window.queue_results)
                        advance.assert_called_once_with(0, window._queue_advance_when_ready)
                    self.assertEqual(logged.call_count, 4)
            finally:
                window.close()

    def test_shared_drop_zone_has_separate_browse_actions(self):
        from .gui import QFileDialog
        window = ComicWindow()
        try:
            with patch.object(QFileDialog, "getOpenFileNames", return_value=([], "")) as files:
                window.import_zone.files_button.click()
                files.assert_called_once()
            with patch.object(QFileDialog, "getExistingDirectory", return_value="") as folders:
                window.import_zone.folder_button.click()
                folders.assert_called_once()
        finally:
            window.close()

    def test_final_batch_confirmation_can_review_without_exporting(self, review_batch=True):
        from PySide6.QtWidgets import QPushButton
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (60, 80), "red").save(folder / "001.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                window.show_export()
                window.book_name.setText("Last book")
                window.batch_import_active = True
                def review(dialog):
                    next(b for b in dialog.findChildren(QPushButton)
                         if b.text() == ("Review batch…" if review_batch else "Queue comic and start exports")).click()
                    return QDialog.Accepted
                with patch.object(QDialog, "exec", review), patch.object(QTimer, "singleShot") as schedule:
                    window.add_to_queue()
                self.assertEqual(len(window.queue_jobs), 1)
                self.assertIsNone(window.plan)
                self.assertIsNone(window.active_queue_job)
                self.assertFalse((folder.parent / "Last book.cbz").exists())
                callback = schedule.call_args.args[1]
                if review_batch:
                    with patch.object(window, "show_queue") as show:
                        callback()
                        show.assert_called_once_with(batch_review=True)
                else:
                    self.assertEqual(callback, window.start_queue)
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_final_batch_confirmation_still_starts_directly(self):
        self.test_final_batch_confirmation_can_review_without_exporting(review_batch=False)

    def test_batch_review_removal_keeps_source_and_disables_empty_export(self):
        from PySide6.QtWidgets import QPushButton, QListWidget
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "001.png"
            Image.new("RGB", (60, 80), "red").save(source)
            original = source.read_bytes()
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                window.show_export()
                window.book_name.setText("Queued book")
                with patch.object(window, "confirm_export", return_value=True):
                    window.add_to_queue()
                def remove(dialog):
                    listing = dialog.findChild(QListWidget)
                    self.assertIn("Queued book.cbz", listing.item(0).text())
                    self.assertIn("Full:", listing.item(0).text())
                    listing.setCurrentRow(0)
                    buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
                    buttons["Remove selected"].click()
                    self.assertEqual(listing.count(), 0)
                    self.assertFalse(buttons["Start export"].isEnabled())
                    return QDialog.Rejected
                with patch.object(QDialog, "exec", remove):
                    window.show_queue(batch_review=True)
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(window.queue_jobs)
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_add_another_batch_item_returns_to_import_without_exporting(self):
        from .gui import QFileDialog
        window = ComicWindow()
        try:
            with patch.object(QFileDialog, "getOpenFileNames", return_value=(["/tmp/extra.cbz"], "")), \
                 patch.object(window, "_import_paths") as importer, \
                 patch.object(window, "start_queue") as export:
                window._add_batch_items()
                importer.assert_called_once_with([Path("/tmp/extra.cbz")])
                self.assertTrue(window.batch_import_active)
                export.assert_not_called()
            window.batch_import_active = False
            with patch.object(QFileDialog, "getExistingDirectory", return_value=""), \
                 patch.object(window, "_import_paths") as importer:
                window._add_batch_items(folder=True)
                importer.assert_not_called()
                self.assertFalse(window.batch_import_active)
        finally:
            window.close()

    def test_two_reviewed_comics_queue_and_export_sequentially(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = ComicWindow()
            try:
                window.show()
                window.encoder_workers.setValue(1)
                for index in (1, 2):
                    folder = root / f"book-{index}"
                    folder.mkdir()
                    for page in range(2):
                        Image.new("RGB", (70, 95), (index * 60, page * 50, 80)).save(
                            folder / f"{page:03d}.png")
                    window.scan_done(scan_manual_folder(folder))
                    window.show_export()
                    window.opt.format = "jpeg" if index == 1 else "png"
                    window.book_name.setText(f"Queued Book {index}")
                    with patch.object(window, "confirm_export", return_value=True):
                        window.add_to_queue()
                    self.assertIsNone(window.plan)
                    self.assertEqual(len(window.queue_jobs), index)
                self.assertEqual([job.options.format for job in window.queue_jobs], ["jpeg", "png"])
                window.start_queue()
                self.wait_for(lambda: window.active_queue_job is None and
                              not window.queue_jobs and window.queue_completed == 2,
                              seconds=20)
                for index in (1, 2):
                    folder = root / f"book-{index}"
                    with zipfile.ZipFile(folder.parent / f"Queued Book {index}.cbz") as archive:
                        self.assertEqual(len(archive.namelist()), 2)
                        self.assertTrue(all(name.endswith(".jpg" if index == 1 else ".png")
                                            for name in archive.namelist()))
                    self.assertEqual(len(list(folder.glob("*.png"))), 2)
            finally:
                window.close()

    def test_queued_archive_discards_staged_pages_and_reimports_for_export(self, keep=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.cbz"
            pages = root / "input"
            pages.mkdir()
            for i in range(2):
                Image.new("RGB", (60, 80), (i * 80, 20, 30)).save(pages / f"{i:03d}.png")
            with zipfile.ZipFile(source, "w") as archive:
                for page in pages.glob("*.png"):
                    archive.write(page, page.name)
            staged = tempfile.TemporaryDirectory()
            staged_path = Path(staged.name)
            scan = import_archive(source, staged_path / "pages", manual=True)
            window = ComicWindow()
            try:
                window.show()
                window.import_work = staged
                window.scan_done(scan)
                window.show_export()
                window.book_name.setText("Queued Archive")
                window.keep_queue_pages.setChecked(keep)
                window.encoder_workers.setValue(1)
                with patch.object(window, "confirm_export", return_value=True):
                    window.add_to_queue()
                self.assertEqual(staged_path.exists(), keep)
                if keep:
                    self.assertEqual(window.queue_jobs[0].import_work, staged)
                    self.assertIsNone(window.queue_jobs[0].reimport_source)
                else:
                    self.assertIsNone(window.queue_jobs[0].import_work)
                    self.assertEqual(window.queue_jobs[0].reimport_source, source)
                window.start_queue()
                self.wait_for(lambda: window.active_queue_job is None and
                              not window.queue_jobs and window.queue_completed == 1,
                              seconds=20)
                self.assertTrue((root / "Queued Archive.cbz").is_file())
                self.assertFalse(staged_path.exists())
            finally:
                window.close()

    def test_queued_archive_can_retain_imported_pages(self):
        self.test_queued_archive_discards_staged_pages_and_reimports_for_export(keep=True)

    def test_queue_reimport_preserves_edits_and_pdf_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "book.pdf"
            source.write_bytes(b"test source identity")
            pages = root / "pages"
            pages.mkdir()
            for i in range(4):
                Image.new("RGB", (60, 80), "white").save(pages / f"{i:03d}.png")
            scan = scan_manual_folder(pages)
            plan = BookPlan(scan)
            plan.manual_pairs.add(0)
            plan.removed_pages.add(3)
            plan.rotations[0] = 90
            stat = source.stat()
            job = QueuedComic(plan, ExportOptions(book_name="Test"), source, None, None, source,
                              (stat.st_size, stat.st_mtime_ns),
                              {"dpi": 300, "workers": 2, "smooth_images": False})
            captured = []
            with patch("comic_builder.gui.import_pdf", return_value=scan) as importer, \
                 patch("comic_builder.gui.export_book", side_effect=lambda p, o, **kw: captured.append(p)):
                export_queued_comic(job)
            self.assertEqual(importer.call_args.kwargs["dpi"], 300)
            self.assertFalse(importer.call_args.kwargs["smooth_images"])
            self.assertEqual(captured[0].manual_pairs, {0})
            self.assertEqual(captured[0].removed_pages, {3})
            self.assertEqual(captured[0].rotations, {0: 90})
            source.write_bytes(b"changed original")
            with self.assertRaisesRegex(ValueError, "changed since review"):
                export_queued_comic(job)

    def test_failed_queued_export_keeps_remaining_job_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(2):
                Image.new("RGB", (60, 80), "white").save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_manual_folder(folder))
                window.show_export()
                window.book_name.setText("Retry Me")
                with patch.object(window, "confirm_export", return_value=True):
                    window.add_to_queue()
                (folder / "000.png").unlink()
                with patch.object(QMessageBox, "warning") as warning:
                    window.start_queue()
                    self.wait_for(lambda: window.active_queue_job is None and
                                  window.pages.currentIndex() == 0 and
                                  window.queue_start_button.isEnabled(), seconds=10)
                self.assertEqual(len(window.queue_jobs), 1)
                self.assertIn("Retry Me", warning.call_args.args[2])
            finally:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.close()

    def test_queue_renames_duplicate_output_before_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (60, 80), "white").save(folder / "000.png")
            window = ComicWindow()
            try:
                for _ in range(2):
                    window.scan_done(scan_manual_folder(folder))
                    window.show_export()
                    window.book_name.setText("Same Name")
                    if not window.queue_jobs:
                        with patch.object(window, "confirm_export", return_value=True):
                            window.add_to_queue()
                    else:
                        outputs = window._preflight_export(window.options_from_screen())
                        self.assertEqual(window.book_name.text(), "Same Name - New export")
                        self.assertEqual(outputs[0].name, "Same Name - New export.cbz")
                        self.assertIn("already exists or is queued", window.export_name_notice)
                        break
            finally:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.close()

    def test_existing_export_renames_all_copies_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "pages"
            folder.mkdir()
            Image.new("RGB", (60, 80), "white").save(folder / "000.png")
            originals = {"Book - Full.cbz": b"existing comic",
                         "Book - New export - Reduced.cbz": b"existing reduced comic"}
            for name, data in originals.items():
                (folder.parent / name).write_bytes(data)
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder)); window.show_export()
                window.book_name.setText("Book")
                window.keep_images.setChecked(True)
                window.opt.size_output = "both"
                opt = window.options_from_screen()
                outputs = window._preflight_export(opt)
                self.assertEqual(opt.book_name, "Book - New export 2")
                self.assertEqual(len(outputs), 4)
                self.assertTrue(all("Book - New export 2 - " in path.name for path in outputs))
                def inspect_confirmation(dialog):
                    from PySide6.QtWidgets import QLabel
                    labels = [label.text() for label in dialog.findChildren(QLabel)]
                    self.assertTrue(any('This export will use “Book - New export 2”' in text
                                        for text in labels))
                    self.assertTrue(any('Book - New export 2 - Full.cbz' in text
                                        for text in labels))
                    return QDialog.Rejected
                with patch.object(QDialog, "exec", inspect_confirmation):
                    self.assertFalse(window.confirm_export(opt))
                export_book(window.plan, opt)
                self.assertTrue(all(path.exists() for path in outputs))
                for name, data in originals.items():
                    self.assertEqual((folder.parent / name).read_bytes(), data)
                window._preflight_export(opt)
                self.assertNotEqual(opt.book_name, "Book - New export 2")
            finally:
                window.close()

    def test_final_review_swap_drag_join_and_component_rotations(self):
        from .gui import PageThumbnail
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(3):
                image = Image.new("RGB", (60, 80), (30 + i * 60, 20, 10))
                image.putpixel((0, 0), (255, i, 0))
                image.save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                window.enter_preview()
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 3)
                window.plan.set_rotation(0, 180)
                window.plan.set_rotation(1, 180)
                window.comparing = (0, 1)
                window.show_preview()
                self.assertTrue(window.swap_button.isEnabled())
                self.assertEqual(window.preview_pair.left.toImage().pixelColor(0, 0).red(), 30)
                window.swap_button.click()
                self.assertEqual(window.plan.entries()[:2], [(1, None), (0, None)])
                self.assertTrue(window.join_button.isEnabled())
                from dataclasses import replace
                window.plan.scan.pages = [replace(page, group=0) for page in window.plan.scan.pages]
                window.join_selected()
                self.assertEqual(window.plan.entries(), [(1, 0), (2, None)])
                self.assertEqual(window.plan.joined_rotations[1], (180, 180))
                source, target = window.thumb_buttons
                self.assertIsInstance(source, PageThumbnail)
                event = Mock()
                event.source.return_value = source
                event.position.return_value.x.return_value = target.width() - 1
                target.dropEvent(event)
                self.wait_for(lambda: window.plan.entries() == [(2, None), (1, 0)])
                event.accept.assert_called_once()
                self.assertEqual(window.preview_index, 1)
                self.assertEqual((window.main_image._pixmap.width(), window.main_image._pixmap.height()), (120, 80))
                window.split_selected()
                self.assertEqual(window.plan.entries(), [(2, None), (1, None), (0, None)])
                self.assertEqual(window.plan.rotations, {1: 180, 0: 180})
            finally:
                window.close()

    def test_drag_insertion_marker_near_edge_does_not_move_far_edge_moves_and_undoes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(4):
                Image.new('RGB', (60, 80), (30 + i * 40, 20, 10)).save(folder / f'{i:03d}.png')
            window = ComicWindow()
            try:
                window.show(); window.scan_done(scan_manual_folder(folder)); window.enter_preview()
                self.wait_for(lambda: len(getattr(window, 'thumb_buttons', [])) == 4)
                self.app.processEvents(); window._resize_thumbnail_strip()
                original = window.plan.entries()
                source, neighbor = window.thumb_buttons[:2]
                near = neighbor.x() + 2
                self.assertTrue(window.update_page_drop(source, near))
                self.assertEqual(window.page_drop_boundary, 1)
                self.assertFalse(window.page_drop_marker.isHidden())
                window.finish_page_drop(source, near); self.app.processEvents()
                self.assertEqual(window.plan.entries(), original)
                self.assertFalse(window.undo_remove_button.isEnabled())
                far = neighbor.x() + neighbor.width() - 2
                window.update_page_drop(source, far)
                self.assertEqual(window.page_drop_boundary, 2)
                edge = (neighbor.x() + neighbor.width() + window.thumb_buttons[2].x()) / 2
                self.assertLess(abs(window.page_drop_marker.geometry().center().x() - edge), 2)
                window.finish_page_drop(source, far)
                self.wait_for(lambda: window.plan.entries()[:2] == [(1, None), (0, None)])
                self.assertTrue(window.undo_remove_button.isEnabled())
                self.assertEqual(window.undo_remove_button.toolTip(), 'Undo page move')
                window.undo_remove_button.click()
                self.assertEqual(window.plan.entries(), original)
                self.assertEqual(window.preview_index, 0)
                self.assertFalse(window.undo_remove_button.isEnabled())
                # Gaps are valid drop targets as well, avoiding a forbidden-drop cursor.
                source = window.thumb_buttons[0]
                self.assertTrue(window.update_page_drop(source, edge))
                window.clear_page_drop()
                self.assertTrue(window.page_drop_marker.isHidden())
                # The insertion sides reverse with the thumbnail strip's reading direction.
                window.plan.scan.right_to_left = True
                window.rebuild_thumbnails(); self.app.processEvents()
                neighbor = window.thumb_buttons[1]; source = window.thumb_buttons[0]
                self.assertTrue(window.update_page_drop(source, neighbor.x() + neighbor.width() - 2))
                self.assertEqual(window.page_drop_boundary, 1)
                self.assertTrue(window.update_page_drop(source, neighbor.x() + 2))
                self.assertEqual(window.page_drop_boundary, 2)
            finally:
                window.close()

    def test_measured_screenshot_crops_skip_dialog_and_use_normal_pair_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (111, 0, 309, 306))
                image.save(folder / "001.png")
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (11, 0, 408, 306))
                image.save(folder / "002.png")
            original = [(folder / name).read_bytes() for name in ("001.png", "002.png")]
            window = ComicWindow()
            try:
                window.show()
                window.screenshot_mode.setChecked(True)
                with patch.object(ScreenshotCropDialog, "exec") as crop_review:
                    window.start_scan(folder)
                    self.wait_for(lambda: window.plan is not None, seconds=10)
                    crop_review.assert_not_called()
                self.assertEqual([p.width for p in window.plan.scan.pages], [198, 198, 199])
                self.assertFalse(any(c.left == 1 for c in window.plan.scan.candidates))
                self.assertEqual(window.plan.scan.output_folder, folder.parent)
                self.assertEqual([(folder / name).read_bytes() for name in ("001.png", "002.png")], original)
            finally:
                window.close()
                if window.screenshot_stage_dir is not None:
                    shutil.rmtree(window.screenshot_stage_dir, ignore_errors=True)
                    clear_session(folder, diagnostics.log_path().parent.parent)

    def test_crop_review_shows_inferred_double_and_editable_book_size(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            clear, dark = folder / "001.png", folder / "002.png"
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (11, 0, 408, 306))
                image.save(clear)
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("red", (60, 100, 360, 200))
                image.save(dark)
            dialog = ScreenshotCropDialog(propose_crops([clear, dark]))
            try:
                self.assertEqual(dialog.choices[1].kind, "double")
                self.assertTrue(dialog.choices[1].inferred)
                self.assertFalse(dialog.choices[1].flagged)
                self.assertEqual(dialog._guide("double", 1), (11, 0, 408, 306))
                dialog.book_width.setValue(199)
                dialog.set_book_size()
                self.assertEqual(dialog.choices[1].box, (11, 0, 409, 306))
            finally:
                dialog.close()
            middle = folder / "003.png"
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("red", (90, 100, 330, 200))
                image.save(middle)
            all_proposals = propose_crops([clear, middle])
            review = ScreenshotCropDialog([all_proposals[1]], calibration=all_proposals)
            try:
                review.choose("double")
                self.assertTrue(review.choices[0].flagged)
            finally:
                review.close()

    def test_inferred_crop_is_marked_in_final_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("red", (60, 100, 360, 200))
                image.save(source / "001.png")
            staged = stage_screenshots(
                [CropChoice(source / "001.png", "double", (11, 0, 408, 306), True,
                            flagged=True)],
                root / "staged", source)
            window = ComicWindow()
            try:
                window.show()
                window.screenshot_source = source
                window.screenshot_flagged_pages = staged.flagged_pages
                with patch.object(QMessageBox, "information"):
                    window.scan_done(staged.scan)
                    window.enter_preview()
                    self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 2)
                    self.app.processEvents()
                self.assertIn("check unusual-width screenshot", window.thumb_buttons[0].toolTip())
                self.assertIn("! Check unusual-width screenshot", window.preview_heading.text())
                self.assertIn("#ed3c9b", window.thumb_buttons[0].styleSheet())
            finally:
                window.close()

    def test_arrow_shortcut_works_when_scan_opens_review(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(3):
                Image.new("RGB", (70, 95), "black").save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.review_mode.setCurrentIndex(1)
                window.start_scan(folder)
                self.wait_for(lambda: window.pages.currentWidget() == window.review)
                self.wait_for(lambda: QApplication.focusWidget() == window.review)
                QTest.keyClick(QApplication.focusWidget(), Qt.Key_Down)
                self.assertEqual(window.plan.current()[1].left, 1)
            finally:
                window.close()

    def test_file_picker_and_remembered_export_selections(self):
        first = ComicWindow()
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "comic.pdf"
                source.touch()
                with patch("comic_builder.gui.QFileDialog.getOpenFileNames",
                           return_value=([str(source)], "")) as picker, \
                     patch.object(ImportChoiceDialog, "exec", return_value=QDialog.Accepted), \
                     patch.object(first, "start_scan") as start:
                    first.choose_file()
                    self.assertEqual(start.call_args.args[0], source.resolve())
                    self.assertIn("*.cb7", picker.call_args.args[3])
                    self.assertIn("*.pdf", picker.call_args.args[3])
            first.archive_format.setCurrentIndex(1)
            def accept_options(dialog):
                dialog.format.setCurrentText("WebP")
                dialog.spread_format.setCurrentIndex(1)
                dialog.make_full.setChecked(True)
                dialog.make_reduced.setChecked(True)
                dialog.webp_quality.setValue(87)
                dialog.advanced.lossless.setChecked(True)
                dialog.advanced.method.setValue(6)
                dialog.resize_settings.mode.setCurrentText("Width")
                dialog.resize_settings.pixels.setValue(1280)
                dialog.resize_settings.gamma.setChecked(False)
                dialog.resize_settings.filter.setCurrentText("Lanczos")
                return dialog.accept() or dialog.result()
            with patch.object(OutputOptionsDialog, "exec", accept_options):
                first.show_output_options()
            self.assertEqual((first.opt.format, first.opt.spread_format, first.opt.size_output),
                             ("webp", "jpeg", "both"))
            first.new_comic()
            reopened = OutputOptionsDialog(first.opt, first.custom_resize_values)
            try:
                self.assertEqual(reopened.format.currentText(), "WebP")
                self.assertEqual(reopened.advanced.format.currentText(), "WebP")
                self.assertTrue(reopened.make_full.isChecked() and reopened.make_reduced.isChecked())
                self.assertEqual(reopened.resize_settings.pixels.value(), 1280)
                reopened.format.setCurrentText("JPEG")
                reopened.reject()
                self.assertEqual(first.opt.format, "webp")
            finally:
                reopened.close()
        finally:
            first.close()
        second = ComicWindow()
        try:
            self.assertEqual((second.opt.format, second.opt.spread_format, second.opt.size_output),
                             ("webp", "jpeg", "both"))
            self.assertEqual(second.archive_format.currentIndex(), 1)
            dialog = OutputOptionsDialog(second.opt, second.custom_resize_values)
            try:
                self.assertEqual(dialog.format.currentText(), "WebP")
                self.assertEqual(dialog.advanced.format.currentText(), "WebP")
                self.assertEqual(dialog.webp_quality.value(), 87)
                self.assertTrue(dialog.advanced.lossless.isChecked())
                self.assertEqual(dialog.advanced.method.value(), 6)
                self.assertTrue(dialog.make_full.isChecked() and dialog.make_reduced.isChecked())
                self.assertEqual(dialog.resize_settings.mode.currentText(), "Width")
                self.assertEqual(dialog.resize_settings.pixels.value(), 1280)
                self.assertFalse(dialog.resize_settings.gamma.isChecked())
                self.assertEqual(dialog.resize_settings.filter.currentText(), "Lanczos")
            finally:
                dialog.close()
        finally:
            second.close()

    def test_final_preview_touches_selected_unjoined_pages_and_rotates_join(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for i, color in enumerate(("red", "blue")):
                Image.new("RGB", (80, 100), color).save(folder / f"{i:02d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_folder(folder))
                window.enter_preview()
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 2)
                window.selected_first = 0
                with patch.object(QApplication, "keyboardModifiers", return_value=Qt.ShiftModifier):
                    window.thumbnail_clicked(1)
                self.assertTrue(window.preview_pair.isVisible())
                self.assertFalse(window.main_image.isVisible())
                left, right = window.preview_pair.image_rects()
                self.assertAlmostEqual(left.right(), right.left())
                original_join = window.plan.join
                with patch.object(window.plan, "join", side_effect=lambda left, right=None:
                                  original_join(left, override_boundary=True, right=right)):
                    window.join_selected()
                window.rotate_preview_page(90)
                self.assertEqual(window.plan.rotations, {0: 90})
                self.assertEqual(window.thumb_keys[0][2], 90)
                window.split_selected()
                self.assertEqual(window.plan.rotations, {0: 90, 1: 90})
                self.assertTrue(window.rotate_preview_right.isEnabled())
                window.rotate_preview_page(90)
                self.assertEqual(window.plan.rotations, {0: 180, 1: 90})
                self.assertEqual(window.thumb_keys[0][2], 180)
            finally:
                window.close()

    def test_override_join_keeps_joined_thumbnail_in_view(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for index in range(30):
                Image.new("RGB", (60, 80), "black").save(folder / f"{index:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_manual_folder(folder))
                window.enter_preview()
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 30)
                window.thumbnail_clicked(23)
                with patch.object(QApplication, "keyboardModifiers", return_value=Qt.ShiftModifier):
                    window.thumbnail_clicked(24)
                def approve(dialog):
                    next(button for button in dialog.buttons()
                         if button.text() == "Join anyway").click()
                    return dialog.result()
                with patch.object(QMessageBox, "exec", approve):
                    window.join_selected()
                self.assertEqual(window.plan.entries()[23], (23, 24))
                self.assertEqual(window.preview_index, 23)
                self.wait_for(lambda: len(window.thumb_buttons) == 29)
                self.app.processEvents()
                self.app.processEvents()
                button = window.thumb_buttons[23]
                bar = window.thumb_area.horizontalScrollBar()
                self.assertLessEqual(abs(button.x() + button.width() // 2 - bar.value() -
                                         window.thumb_area.viewport().width() // 2), 3)
            finally:
                window.close()

    def test_resume_folder_skips_rescan_and_restores_manual_spread(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "book"
            folder.mkdir()
            for i in range(3):
                Image.new("RGB", (60, 80), "orange").save(folder / f"{i:03d}.png")
            with patch("comic_builder.gui.diagnostics.log_path", return_value=root / "app" / "logs" / "run.log"):
                first = ComicWindow()
                try:
                    first.show()
                    first.session_source = folder
                    first.scan_done(scan_folder(folder))
                    first.plan.join(0, override_boundary=True)
                    first._save_review_report()
                finally:
                    first.close()
                second = ComicWindow()
                try:
                    second.show()
                    def resume(dialog):
                        next(button for button in dialog.buttons()
                             if button.text() == "Resume review").click()
                        return dialog.result()
                    with patch.object(QMessageBox, "exec", resume), \
                         patch("comic_builder.gui.scan_folder", side_effect=AssertionError("Rescanned")):
                        second.start_scan(folder)
                    self.assertIn(0, second.plan.manual_pairs)
                    self.assertEqual(len(second.plan.scan.pages), 3)
                finally:
                    second.close()

    def test_export_screen_estimates_size_and_can_be_turned_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'pages'; folder.mkdir()
            for n in range(4):
                Image.effect_noise((200, 300), 40 + n).convert('RGB').save(folder / f'{n:03}.png')
            window = ComicWindow()
            try:
                window.show()
                window.estimate_size.setChecked(True)
                window.scan_done(scan_folder(folder, manual=True))
                window.show_export()
                self.wait_for(lambda: window.size_estimate.text().startswith('About'), seconds=20)
                self.assertRegex(window.size_estimate.text(), r'About [0-9.,]+ [KMG]B')
                window.estimate_size.setChecked(False)
                self.assertEqual(window.size_estimate.text(), '')
                self.assertFalse(window.settings.value('estimate_size', True, type=bool))
                window.estimate_size.setChecked(True)
                self.assertEqual(window.size_estimate.text(), 'Estimating…')
                self.wait_for(lambda: window.size_estimate.text().startswith('About'), seconds=20)
            finally:
                window.close()

    def test_progress_window_makes_room_for_a_longer_message(self):
        window = ComicWindow()
        try:
            window.show()
            window.progress_source.setText("X-O Manowar - Unconquered 004.pdf")
            window.progress_label.setText("Rendering PDF pages: 4 of 36…")
            window.pages.setCurrentWidget(window.progress_page)
            window._compact_window()
            QTest.qWait(400)
            window.resize(50, 50)  # As narrow and short as it will go.
            QTest.qWait(100)
            one_line = window.height()
            window.progress_label.setText("Scanning 36 rendered pages… 1% complete")
            QTest.qWait(100)
            self.app.processEvents()
            label = window.progress_label
            self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
            self.assertGreaterEqual(window.progress_bar.geometry().top(), label.geometry().bottom())
            self.assertGreaterEqual(window.height(), one_line)
        finally:
            window.close()

    def test_output_option_tabs_have_the_same_border(self):
        from PySide6.QtWidgets import QScrollArea
        window = ComicWindow()
        self.addCleanup(window.close)
        dialog = OutputOptionsDialog(ExportOptions("Test"), window.custom_resize_values)
        try:
            tabs = dialog.findChild(QTabWidget)
            resize = tabs.widget(1)
            self.assertIsInstance(resize, QScrollArea)
            self.assertEqual(resize.frameShape(), QFrame.NoFrame)  # No second line inside the tab's border.
        finally:
            dialog.reject()

    def test_start_screen_cannot_be_squeezed(self):
        window = ComicWindow()
        try:
            window.show()
            QTest.qWait(400)
            window.resize(40, 40)
            QTest.qWait(50)
            minimum = window.start_page.minimumSizeHint()
            self.assertGreaterEqual(window.width(), minimum.width())
            self.assertGreaterEqual(window.height(), minimum.height())
        finally:
            window.close()

    def test_export_screen_cannot_be_squeezed_into_overlapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'pages'; folder.mkdir()
            for n in range(2):
                Image.new('RGB', (80, 120), 'white').save(folder / f'{n:03}.png')
            window = ComicWindow()
            try:
                window.estimate_size.setChecked(False)
                window.scan_done(scan_folder(folder, manual=True))
                window.show_export()
                window.show()
                self.wait_for(lambda: window.export_scroll.minimumHeight() > 0)
                QTest.qWait(400)  # Let any delayed window sizing finish first.
                window.resize(window.width(), 150)  # Dragging the bottom edge far up.
                QTest.qWait(50)
                self.app.processEvents()
                scroll = window.export_scroll.geometry()
                hint = window.export_hint.geometry()
                # The footer stays below the settings, and the settings keep their full height.
                self.assertGreaterEqual(hint.top(), scroll.bottom())
                self.assertGreaterEqual(window.create_button.geometry().top(), hint.bottom())
                content = window.export_scroll.widget()
                self.assertGreaterEqual(window.export_scroll.viewport().height() + 1,
                                        min(content.minimumSizeHint().height(), window.export_scroll.minimumHeight()))
                self.assertGreater(window.height(), 150)
                # Not too narrow either.
                window.resize(100, window.height())
                QTest.qWait(50)
                self.assertGreaterEqual(window.width(), window.export_page.minimumSizeHint().width())
                # Leaving for the small progress screen doesn't keep the export screen's height.
                export_minimum = window.pages.minimumHeight()
                window.pages.setCurrentWidget(window.progress_page)
                window._compact_window()
                QTest.qWait(400)
                self.assertLess(window.height(), export_minimum)
                window.progress_label.setText("Rendering PDF pages: 4 of 36…")
                window.resize(50, 50)
                QTest.qWait(50)
                self.assertGreaterEqual(window.width(), 360)
                self.assertGreaterEqual(window.height(), window.progress_page.minimumSizeHint().height())
                # The next step's longer message still fits on one line at the smallest width,
                # and a message that does wrap (a long name) gets the room it needs.
                narrowest = window.width()
                window.progress_label.setText("Scanning 36 rendered pages… 1% complete")
                QTest.qWait(50)
                self.assertEqual(window.width(), narrowest)
                one_line = window.progress_label.fontMetrics().height()
                self.assertLess(window.progress_label.heightForWidth(window.progress_label.width()), 2 * one_line)
                window.progress_label.setText("Scanning " + "A very long folder name " * 6 + "…")
                QTest.qWait(50)
                label = window.progress_label
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                self.assertGreaterEqual(window.progress_bar.geometry().top(), label.geometry().bottom())
                # The full-screen review screens may still be made small.
                window.return_preview_from_export()
                self.assertEqual(window.pages.minimumHeight(), 0)
            finally:
                window.close()

    def test_pdf_dimensions_follow_dpi_and_selected_page(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "two-pages.pdf"
            with Image.new("RGB", (72, 100), "orange") as first:
                with Image.new("RGB", (144, 90), "blue") as second:
                    first.save(source, "PDF", save_all=True,
                               append_images=[second], resolution=72)
            dialog = PdfImportDialog(source, 200, 0)
            try:
                dialog.show()
                self.wait_for(lambda: dialog.current_size is not None and dialog.resolution_checked)
                self.assertEqual(dialog.page.maximum(), 2)
                # The pages are 72 dpi images, so the resolution is set to match them.
                self.assertEqual(dialog.dpi.value(), 72)
                self.assertFalse(dialog.dpi_chosen_by_user)
                self.assertIn("about 72 dpi", dialog.dpi_note.text())
                self.assertIn("usual setting is 200 dpi", dialog.dpi_note.text())
                self.assertIn("72 × 100 px", dialog.dimensions.text())
                dialog.dpi.setValue(200)
                self.assertTrue(dialog.dpi_chosen_by_user)
                self.assertIn("200 × 278 px", dialog.dimensions.text())
                self.assertIn("without adding detail", dialog.dpi_note.text())
                dialog._resolution_found(150)  # A late result never overrides the user's choice.
                self.assertEqual(dialog.dpi.value(), 200)
                dialog.dpi.setValue(300)
                self.assertIn("300 × 417 px", dialog.dimensions.text())
                dialog.page.setValue(2)
                self.wait_for(lambda: dialog.current_size == (144.0, 90.0))
                self.assertIn("600 × 375 px", dialog.dimensions.text())
                dialog.smooth_images.setChecked(False)
                self.wait_for(lambda: dialog.current_size is not None)
                self.assertFalse(dialog.smooth_images.isChecked())
            finally:
                dialog.reject()
                self.assertFalse(dialog.preview_worker.isRunning())

    def test_pdf_scan_progress_counts_pages_and_hides_internal_steps(self):
        window = ComicWindow()
        try:
            window._pdf_progress(128, 172 * 3)
            self.assertIn("128 of 172", window.progress_label.text())
            window._pdf_progress(172 + 285, 172 * 3)
            self.assertEqual(window.progress_label.text(),
                             "Scanning 172 rendered pages… 83% complete")
        finally:
            window.close()

    def test_screenshot_staging_progress_names_cropping_and_scanning(self):
        window = ComicWindow()
        try:
            window._screenshot_stage_progress(42, 138, 271)
            self.assertEqual(window.progress_label.text(),
                             "Cropping screenshots: 42 of 138…")
            window._screenshot_stage_progress(207, 138, 271)
            self.assertEqual(window.progress_label.text(),
                             "Scanning 271 cropped pages for Pair Review… 50% complete")
            window.review_mode.setCurrentIndex(1)
            window._screenshot_stage_progress(276, 138, 271)
            self.assertEqual(window.progress_label.text(),
                             "Reading 271 cropped pages for Pair Review… 100% complete")
        finally:
            window.close()

    def test_parallel_export_from_gui_worker_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(3):
                Image.new("RGB", (128, 180), (i * 50, 70, 200)).save(folder / f"p{i:02d}.png")
            plan = BookPlan(scan_folder(folder))
            worker = Worker(export_book, plan, ExportOptions("Threaded WebP", format="webp", workers=2))
            outputs, errors = [], []
            worker.result.connect(outputs.append)
            worker.failure.connect(errors.append)
            try:
                worker.start()
                self.wait_for(lambda: bool(outputs or errors), seconds=12)
                self.assertFalse(errors)
                self.assertTrue(outputs[0].exists())
            finally:
                self.assertTrue(worker.wait(12000))

    def test_archive_import_uses_archive_title_and_removes_staging_on_close(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            page = folder / "page.png"
            Image.new("RGB", (80, 100), "blue").save(page)
            archive = folder / "My Comic.cbz"
            with zipfile.ZipFile(archive, "w") as writer:
                writer.write(page, "nested/1.png")
            window = ComicWindow()
            try:
                window.show()
                window.start_scan(archive)
                self.wait_for(lambda: window.plan is not None)
                self.assertEqual(window.book_name.text(), "My Comic - Rebuilt")
                self.assertEqual(window.plan.scan.source_archive, archive)
                self.assertEqual(window.plan.scan.output_folder, folder)
                work = Path(window.import_work.name)
                self.assertTrue(work.exists())
                self.wait_for(lambda: not window.worker.isRunning())
            finally:
                window.close()
            self.assertFalse(work.exists())

    def test_pdf_import_shows_rendering_progress_then_uses_pdf_title(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "Print Version.pdf"
            with Image.new("RGB", (90, 110), "green") as first:
                with Image.new("RGB", (90, 110), "yellow") as second:
                    first.save(source, "PDF", save_all=True, append_images=[second], resolution=72)
            window = ComicWindow()
            try:
                window.show()
                window.start_scan(source, pdf_dpi=72, pdf_workers_count=1)
                self.assertIn("Rendering", window.progress_label.text())
                self.wait_for(lambda: window.plan is not None)
                self.assertEqual(window.book_name.text(), "Print Version")
                self.assertEqual(window.plan.scan.source_archive, source)
                self.assertEqual(window.plan.scan.output_folder, folder)
                self.assertEqual([p.path.name for p in window.plan.scan.pages], ["0001.png", "0002.png"])
                self.wait_for(lambda: not window.worker.isRunning())
            finally:
                window.close()

    def test_export_confirmation_starts_only_after_accept(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (80, 100), "orange").save(folder / "p01.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_folder(folder))
                window.show_export()
                QTimer.singleShot(0, lambda: next(w for w in self.app.topLevelWidgets()
                                                 if w.windowTitle() == "Review comic export" and w.isVisible()).accept())
                window.start_export()
                self.assertIs(window.pages.currentWidget(), window.progress_page)
                self.wait_for(lambda: window.done_button.isVisible(), seconds=12)
                self.assertTrue((folder.parent / f"{folder.name}.cbz").exists())
                (folder.parent / f"{folder.name}.cbz").unlink()
                self.wait_for(lambda: not window.worker.isRunning())
                same_plan = window.plan
                same_report = window.review_report_path
                self.assertTrue(window.repeat_button.isVisible())
                window.repeat_export()
                self.assertIs(window.pages.currentWidget(), window.export_page)
                self.assertIs(window.plan, same_plan)
                self.assertEqual(window.review_report_path, same_report)
                self.assertEqual(window.book_name.text(), f"{folder.name} - Test 2")
                def set_output():
                    dialog = next(w for w in self.app.topLevelWidgets()
                                  if isinstance(w, OutputOptionsDialog) and w.isVisible())
                    dialog.make_reduced.setChecked(True)
                    dialog.make_full.setChecked(False)
                    dialog.reduced_format.setCurrentText("WebP")
                    dialog.resize_settings.pixels.setValue(64)
                    dialog.accept()

                QTimer.singleShot(0, set_output)
                window.show_output_options()
                QTimer.singleShot(0, lambda: next(w for w in self.app.topLevelWidgets()
                                                 if w.windowTitle() == "Review comic export" and w.isVisible()).accept())
                window.start_export()
                self.wait_for(lambda: window.done_button.isVisible(), seconds=12)
                second = folder.parent / f"{folder.name} - Test 2.cbz"
                self.assertTrue(second.exists())
                with zipfile.ZipFile(second) as archive:
                    expected_page = f"{folder.name} - Test 2 - 01.webp"
                    self.assertEqual(archive.namelist(), [expected_page])
                    with archive.open(expected_page) as member, Image.open(member) as reduced:
                        self.assertEqual(reduced.size, (51, 64))
                second.unlink()
                self.wait_for(lambda: not window.worker.isRunning())
                window.new_comic()
                next_book = folder / "next book"
                next_book.mkdir()
                Image.new("RGB", (80, 100), "blue").save(next_book / "01.png")
                window.scan_done(scan_folder(next_book))
                window.show_export()
                next_options = OutputOptionsDialog(window.opt, window.custom_resize_values)
                try:
                    self.assertEqual(next_options.reduced_format.currentText(), "WebP")
                    self.assertFalse(next_options.make_full.isChecked())
                    self.assertTrue(next_options.make_reduced.isChecked())
                    self.assertEqual(next_options.resize_settings.pixels.value(), 64)
                finally:
                    next_options.close()
            finally:
                window.close()

    def test_thumbnails_render_and_scroll_independently(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(24):
                Image.new("RGB", (80, 100), "white").save(folder / f"page{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                self.app.processEvents()
                self.assertEqual((window.width(), window.height()), (750, 500))
                window.scan_done(scan_folder(folder))
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 24)
                self.assertTrue(window.isMaximized())
                self.assertEqual(len(window.thumb_buttons), 24)
                self.wait_for(lambda: not window.thumb_buttons[0].icon().isNull())
                self.assertFalse(window.thumb_buttons[0].icon().isNull())
                self.assertGreater(window.thumb_content.width(), 24 * 100)
                self.assertLessEqual(window.thumb_buttons[-1].geometry().right(), window.thumb_content.width())
                bar = window.thumb_area.horizontalScrollBar()
                self.assertGreater(bar.maximum(), 0)
                viewport = window.thumb_area.viewport()
                for index in (0, 12, 23):
                    window.preview_index = index
                    window.show_preview(follow=True)
                    button = window.thumb_buttons[index]
                    center = button.mapTo(viewport, button.rect().center()).x()
                    self.assertLessEqual(abs(center - viewport.rect().center().x()), 2)
                window.preview_index = 23
                window.show_preview(follow=True)
                self.assertGreater(bar.value(), 0)
                bar.setValue(0)
                self.assertEqual(window.preview_index, 23)
                window.thumbnail_clicked(2)
                self.assertEqual(bar.value(), 0)
                self.assertEqual(window.preview_index, 2)
                window.show_export()
                self.app.processEvents()
                # 800 x 560, or taller if this system's fonts need more room.
                self.assertEqual(window.width(), max(800, window.export_page.minimumSizeHint().width()))
                self.assertGreaterEqual(window.height(), 560)
                self.assertGreaterEqual(window.export_hint.geometry().top(), window.export_scroll.geometry().bottom())
                self.assertTrue(window.create_button.isVisible())
                self.assertTrue(window.export_hint.isVisible())
                window.book_name.setText("Sample v02")
                window.start_number.setCurrentIndex(1)
                window.padding.setCurrentIndex(2)
                self.assertEqual(window.naming.itemText(0), "Number only (000.jpg)")
                self.assertEqual(window.naming.itemText(1), "Sample v02 - 000.jpg")
                window.naming.setCurrentIndex(1)
                window.book_name.setText("Revised Comic")
                self.assertEqual(window.naming.currentIndex(), 1)
                self.assertEqual(window.naming.itemText(1), "Revised Comic - 000.jpg")
                window.encoder_workers.setValue(2)
                self.assertEqual(window.options_from_screen().workers, 2)
                self.assertEqual(int(window.settings.value("encoder_workers")), 2)
                window.handling.setCurrentIndex(1)
                self.assertEqual(window.options_from_screen().size_output, "full")

                def set_resize():
                    dialog = next(w for w in self.app.topLevelWidgets()
                                  if isinstance(w, OutputOptionsDialog) and w.isVisible())
                    dialog.make_reduced.setChecked(True)
                    self.assertFalse(dialog.spread_format.isHidden())  # Copying unmodified pages.
                    self.assertTrue(dialog.format.isHidden())
                    self.assertFalse(dialog.reduced_format.isHidden())
                    dialog.resize_settings.mode.setCurrentText("Width")
                    dialog.resize_settings.pixels.setValue(1400)
                    dialog.resize_settings.gamma.setChecked(False)
                    dialog.accept()

                QTimer.singleShot(0, set_resize)
                window.show_output_options()
                self.assertEqual(window.options_from_screen().size_output, "both")
                self.assertEqual(window.options_from_screen().resize_pixels, 80)
                # Target cannot exceed the largest final page width.
                self.assertEqual(window.options_from_screen().resize_mode, "width")
                self.assertFalse(window.options_from_screen().resize_gamma)
                review = window.export_review_text(window.options_from_screen())
                self.assertIn("Full.cbz", review)
                self.assertIn("Reduced.cbz", review)
                self.assertIn("80 px", review)
                QTimer.singleShot(0, lambda: next(w for w in self.app.topLevelWidgets()
                                                 if w.windowTitle() == "Review comic export").reject())
                window.start_export()
                self.assertIs(window.pages.currentWidget(), window.export_page)
                self.assertFalse(any(folder.glob("*.cbz")))
                def cancel_output():
                    dialog = next(w for w in self.app.topLevelWidgets()
                                  if isinstance(w, OutputOptionsDialog) and w.isVisible())
                    dialog.make_reduced.setChecked(False)
                    dialog.format.setCurrentText("PNG")
                    dialog.advanced.progressive.setChecked(True)
                    dialog.reject()

                QTimer.singleShot(0, cancel_output)
                window.show_output_options()
                self.assertEqual(window.options_from_screen().resize_pixels, 80)
                # Target cannot exceed the largest final page width.
                self.assertEqual(window.options_from_screen().size_output, "both")
                self.assertEqual(window.options_from_screen().format, "jpeg")
                self.assertFalse(window.options_from_screen().jpeg_progressive)
                window.encoder_workers.setValue(0)
                window.return_preview_from_export()
                self.assertTrue(window.isMaximized())
            finally:
                window.close()

    def test_tooltips_wrap_to_short_lines(self):
        wrapped = tooltip_text("One two three " * 30)
        self.assertTrue(all(len(line) <= 90 for line in wrapped.splitlines()))

    def test_join_anyway_dialog_for_border_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in (46, 47):
                Image.new("RGB", (80, 100), "black").save(folder / f"page{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_folder(folder))
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 2)
                window.comparing = (0, 1)
                window.show_preview()
                self.assertTrue(window.join_button.isEnabled())

                def confirm():
                    boxes = [w for w in self.app.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()]
                    self.assertEqual(len(boxes), 1)
                    button = next(b for b in boxes[0].buttons() if b.text() == "Join anyway")
                    button.click()

                QTimer.singleShot(0, confirm)
                window.join_selected()
                self.assertEqual(window.plan.entries(), [(0, 1)])
            finally:
                window.close()

    def test_pair_review_displays_after_background_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(2):
                page = Image.new("RGB", (120, 140), "orange")
                for y in range(140):
                    for x in list(range(15)) + list(range(105, 120)):
                        page.putpixel((x, y), ((x * 17 + y * 3) % 256, y % 256, 90))
                page.save(folder / f"page{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_folder(folder))
                self.assertIs(window.pages.currentWidget(), window.review)
                self.assertEqual(window.progress_bar.value(), 100)
                self.wait_for(lambda: window.joined_review.left is not None)
                self.assertIsNotNone(window.joined_review.right)
                left, right = window.joined_review.image_rects()
                self.assertAlmostEqual(left.right(), right.left())
                self.assertAlmostEqual(left.top(), right.top())
                self.assertAlmostEqual(left.height(), right.height())
                self.assertTrue(window.review_report_path.exists())
                window.decide(True)
                data = json.loads(window.review_report_path.read_text(encoding="utf-8"))
                self.assertEqual(data["counts"]["approved"], 1)
            finally:
                window.close()

    def test_possible_choice_defers_without_rejecting_and_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(3):
                Image.new("RGB", (80, 100), "#c06020").save(folder / f"page{i:03d}.png")
            scan = scan_folder(folder)
            scan.candidates = [Candidate(0, 1, .8, True), Candidate(1, 2, .45, False)]
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan)
                window.decide(False)
                self.assertIs(window.pages.currentWidget(), window.possible_choice)
                self.assertIn("1 lower-scoring", window.possible_message.text())
                window._defer_possible()
                self.assertIs(window.pages.currentWidget(), window.preview)
                data = json.loads(window.review_report_path.read_text(encoding="utf-8"))
                self.assertEqual([c["status"] for c in data["candidates"]], ["rejected", "deferred"])
                window.return_review()
                self.assertIs(window.pages.currentWidget(), window.review)
                window.decide(True)
                self.assertIs(window.pages.currentWidget(), window.preview)
                data = json.loads(window.review_report_path.read_text(encoding="utf-8"))
                self.assertEqual([c["status"] for c in data["candidates"]], ["rejected", "approved"])
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
