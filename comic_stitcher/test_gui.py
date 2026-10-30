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

from .backend import BookPlan, Candidate, scan_folder, scan_manual_folder
from .export import ExportOptions, export_book
from .archive_import import import_archive
from .screenshot_geometry import propose_crops
from .screenshot_import import CropChoice, stage_screenshots
from .session import clear_session
from . import diagnostics
from .window_outline import OutlinedDialog
from .gui import (START_SIZE, ComicWindow, ui_app_style, ImportChoiceDialog, OutputOptionsDialog, PdfImportDialog,
                  ScreenshotCropDialog, Worker, tooltip_text,
                  QueuedComic, export_queued_comic, queued_outputs)


class ThumbnailTest(unittest.TestCase):
    original_notice = staticmethod(ComicWindow.show_startup_notice)
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        ui_app_style(cls.app)

    def setUp(self):
        self.real_crop_summary = ComicWindow.screenshot_crop_summary
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
        settings_patch = patch("comic_stitcher.gui.QSettings", side_effect=lambda *_:
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
            self.assertEqual(window.book_scroll.verticalScrollBar().maximum(), 0,
                             [(c.minimumHeight(), c.height()) for c in (window.naming, window.start_number, window.padding)])
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

    def test_one_worker_setting_covers_every_job(self):
        window = ComicWindow()
        try:
            def accept_settings(dialog):
                from PySide6.QtWidgets import QSpinBox, QCheckBox
                controls = dialog.findChildren(QSpinBox)
                self.assertEqual([c.objectName() for c in controls], ["workers"])
                controls[0].setValue(1)
                dialog.findChildren(QCheckBox)[0].setChecked(False)
                return QDialog.Accepted
            with patch.object(QDialog, "exec", accept_settings):
                window.show_settings()
            self.assertEqual(window.settings.value("workers", type=int), 1)
            self.assertEqual(window.worker_count(), 1)
            self.assertEqual(window.options_from_screen().workers, 1)
            self.assertTrue(window.settings.value("hide_source_notice", type=bool))
        finally:
            window.settings.setValue("workers", 0)
            window.close()

    def test_earlier_worker_settings_become_one(self):
        window = ComicWindow()
        try:
            window.settings.remove("workers")
            for key, value in (("pdf_workers", 2), ("screenshot_workers", 3), ("encoder_workers", 4)):
                window.settings.setValue(key, value)
            window._merge_worker_settings()
            self.assertEqual(window.worker_count(), 4)  # The encoding count carries over.
            for key in ("pdf_workers", "screenshot_workers", "encoder_workers"):
                self.assertFalse(window.settings.contains(key), key)
            window.settings.setValue("encoder_workers", 2)
            window._merge_worker_settings()  # Only the first time: the setting is kept after.
            self.assertEqual(window.worker_count(), 4)
        finally:
            window.settings.setValue("workers", 0)
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

    def test_summary_names_screenshots_of_a_wider_or_smaller_page_size(self):
        from .screenshot_geometry import CropProposal
        from dataclasses import replace
        summary = self.real_crop_summary  # setUp stubs the summary out.
        window = ComicWindow()
        try:
            canvas = (4200, 3056)
            stray = [CropProposal(Path(f"{i}.png"), canvas, "single", (1106, 0, 3094, 3056), None, "measured")
                     for i in range(4)]
            stray[2] = CropProposal(Path("2.png"), canvas, "single", (1093, 0, 3107, 3056), None,
                                    "measured", wider_than=1988)
            mixed = [CropProposal(Path(f"{i}.png"), canvas, "single", (1106, 0, 3094, 3056), None, "measured",
                                  family_widths=(1988, 2014), family_width=1988) for i in range(5)]
            for i in (1, 3):
                mixed[i] = replace(mixed[i], box=(1093, 0, 3107, 3056), family_width=2014)
            mixed[3] = replace(mixed[3], source="suggested", by_position=True)
            for proposals, expected in ((stray, "1 screenshot was individually measured at 2014 × 3056"),
                                        (mixed, "2 screenshots have pages measuring 2014 × 3056")):
                shown = []
                with patch.object(QMessageBox, "exec", lambda box: shown.append((box.text(), box.informativeText(), box.icon()))):
                    summary(window, proposals, [])
                self.assertEqual(shown[0][0], "Ready to crop 5 screenshots" if proposals is mixed else "Ready to crop 4 screenshots")
                self.assertEqual(shown[0][2], QMessageBox.NoIcon)  # Not an error.
                self.assertIn(expected, shown[0][1])
            self.assertIn("3 screenshots have pages measuring 1988 × 3056", shown[0][1])
            self.assertIn("1 has black borders but was placed here because of nearby 2014 × 3056 screenshots "
                          "and its border widths", shown[0][1])
            self.assertIn("Note: Multiple page sizes may mean", shown[0][1])
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
                 patch("comic_stitcher.ui.screenshot_flow.ScreenshotCropDialog") as dialog, \
                 patch.object(window, "_stage_approved_screenshots") as stage:
                dialog.return_value.exec.return_value = QDialog.Accepted
                dialog.return_value.approved_choices.return_value = [choice]
                dialog.return_value.book_family = None
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
            with patch("comic_stitcher.gui.Image.open", side_effect=AssertionError("GUI decoded an image")):
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
        with patch("comic_stitcher.gui.QApplication") as application, \
             patch("comic_stitcher.gui.ComicWindow") as window, \
             patch("comic_stitcher.gui.LaunchBroker") as broker, \
             patch("comic_stitcher.gui.sys.argv", ["ComicStitcher.exe", "C:/My Comics/Book.cbz"]):
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
            dialog = PdfImportDialog(source, 200, right_to_left=True)
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

    def test_pair_review_seam_lines_up_the_pair_before_deciding(self):
        from .gui import OverlapDialog
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (120, 160), "red").save(folder / "001.png")
            Image.new("RGB", (120, 160), "blue").save(folder / "002.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                seen = {}

                def line_up(dialog):
                    self.assertIsInstance(dialog, OverlapDialog)
                    seen["ok"] = dialog.ok_button.text()
                    dialog.pixels.setValue(5)
                    QTest.keyClick(dialog, Qt.Key_Right)  # Arrow keys choose the page on top.
                    QTest.keyClick(dialog, Qt.Key_Left)
                    return QDialog.Accepted
                with patch.object(QDialog, "exec", line_up):
                    window.seam_button.click()
                self.assertEqual(seen["ok"], "Use this alignment")
                self.assertIn("lined up in Seam (5 px, left page on top)", window.review_heading.text())
                self.assertEqual((window.joined_review.overlap, window.joined_review.top), (5, "left"))
                with patch.object(QDialog, "exec", lambda dialog: QDialog.Rejected):
                    window.seam_button.click()  # Cancel keeps it.
                self.assertEqual(window.joined_review.overlap, 5)
                window.decide(True)
                self.assertEqual(window.plan.join_overlaps, {0: (5, "left")})
            finally:
                window.close()

    def test_line_up_keys_enter_shows_the_seam_line_and_space_finishes(self):
        from .gui import OverlapDialog
        left, right = Image.new("RGB", (120, 160), "red"), Image.new("RGB", (120, 160), "blue")
        dialog = OverlapDialog(left, right, (0, "left"), find_on_open=False)
        try:
            dialog.show()
            self.app.processEvents()
            image = dialog.image.pixmap().toImage()
            self.assertNotEqual(image.pixelColor(image.width() // 2, 20).name(), "#ffe500")  # Off at first.
            QTest.keyClick(dialog, Qt.Key_Return)
            image = dialog.image.pixmap().toImage()
            self.assertEqual(image.pixelColor(image.width() // 2, 20).name(), "#ffe500")
            self.assertTrue(dialog.isVisible())  # Enter didn't close it.
            QTest.keyClick(dialog, Qt.Key_Return)
            image = dialog.image.pixmap().toImage()
            self.assertNotEqual(image.pixelColor(image.width() // 2, 20).name(), "#ffe500")
            QTest.keyClick(dialog, Qt.Key_I)  # In: one pixel together.
            QTest.keyClick(dialog, Qt.Key_I)
            QTest.keyClick(dialog, Qt.Key_O)  # Out: one pixel apart.
            self.assertEqual(dialog.overlap(), (1, "left"))
            QTest.keyClick(dialog, Qt.Key_Space)
            self.assertEqual(dialog.result(), QDialog.Accepted)
        finally:
            dialog.close()

    def test_final_seam_opens_line_up_for_two_selected_pages(self):
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
                opened = []
                with patch.object(QDialog, "exec", lambda dialog: opened.append(dialog.ok_button.text())
                                  or QDialog.Rejected):
                    QTest.keyClick(window.join_button, Qt.Key_Space)  # Space is Seam, not the button.
                self.assertEqual(opened, ["Join as spread"])
                self.assertEqual(window.plan.entries(), before)  # Cancelled: nothing joined.

                def join(dialog):
                    dialog.pixels.setValue(4)
                    dialog.set_top("right")
                    return QDialog.Accepted
                with patch.object(QDialog, "exec", join):
                    window.preview_seam_button.click()
                self.assertEqual(window.plan.entries(), [(0, 1)])
                self.assertEqual(window.plan.join_overlaps, {0: (4, "right")})
                window.undo_preview_removal()  # One undo removes the join and its alignment.
                self.assertEqual((window.plan.entries(), window.plan.join_overlaps), (before, {}))
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

    def test_pdf_asks_for_page_format_and_is_kept_only_for_the_session(self):
        from .gui import ARCHIVE_CHOICES
        pdf, cb7 = ARCHIVE_CHOICES.index("pdf"), ARCHIVE_CHOICES.index("cb7")
        first = ComicWindow()
        try:
            # Usual settings: WebP pages in a CB7.
            first.opt.format = "webp"
            first.opt.reduced_settings = {"format": "webp"}
            first._save_output_preferences()
            first.archive_format.setCurrentIndex(cb7)
            seen = []
            def cancel(dialog):
                seen.append(dialog)
                return QDialog.Rejected
            with patch.object(OutputOptionsDialog, "exec", cancel):
                first.archive_format.setCurrentIndex(pdf)
            # Cancel keeps the previous archive format and the usual options.
            self.assertEqual(first.archive_format.currentIndex(), cb7)
            self.assertIs(first.opt, first.usual_opt)
            self.assertEqual(first.opt.format, "webp")
            dialog = seen[0]
            self.assertTrue(dialog.for_pdf)
            self.assertEqual(dialog.windowTitle(), "PDF page format")
            self.assertIn("JPEG and PNG", dialog.pdf_note.text())
            for combo in (dialog.format, dialog.spread_format, dialog.reduced_format):
                self.assertFalse(any(combo.itemText(i).startswith("WebP") for i in range(combo.count())))
            self.assertEqual(dialog.format.currentText(), "JPEG")  # WebP becomes JPEG.
            def choose_png(dialog):
                dialog.format.setCurrentText("PNG")
                dialog.reduced_format.setCurrentText("PNG")
                return dialog.accept() or dialog.result()
            with patch.object(OutputOptionsDialog, "exec", choose_png):
                first.archive_format.setCurrentIndex(pdf)
            self.assertEqual(first.archive_format.currentIndex(), pdf)
            opt = first.options_from_screen()
            self.assertEqual((opt.archive_format, opt.format, opt.reduced_settings["format"]),
                             ("pdf", "png", "png"))
            # Edit options while PDF is chosen opens the PDF window again, with the PDF choices.
            def check_pdf_window(dialog):
                self.assertTrue(dialog.for_pdf)
                self.assertEqual(dialog.format.currentText(), "PNG")
                return QDialog.Rejected
            with patch.object(OutputOptionsDialog, "exec", check_pdf_window):
                first.show_output_options()
            # Saving preferences (as an export does) saves only the usual options.
            first._save_output_preferences()
            # Back to CBZ: the usual WebP options return; PDF's are kept for this session.
            first.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index("cbz"))
            self.assertEqual(first.options_from_screen().format, "webp")
            with patch.object(OutputOptionsDialog, "exec", lambda dialog: dialog.accept() or dialog.result()):
                first.archive_format.setCurrentIndex(pdf)
            self.assertEqual(first.options_from_screen().format, "png")
        finally:
            first.close()
        # Next time Comic Stitcher starts: the last archive format other than PDF, usual options.
        second = ComicWindow()
        try:
            self.assertEqual(second.archive_format.currentIndex(), ARCHIVE_CHOICES.index("cbz"))
            self.assertEqual(second.opt.format, "webp")
            self.assertIsNone(second.pdf_opt)
        finally:
            second.close()

    def test_cbr_is_offered_only_with_rar_and_rar4_only_with_rar_6_or_older(self):
        from .gui import ARCHIVE_CHOICES
        from .rar_export import RarProgram
        cbr = ARCHIVE_CHOICES.index("cbr")
        with patch("comic_stitcher.ui.export_screen.find_rar", return_value=None):
            window = ComicWindow()
        try:
            self.assertTrue(window.archive_format.view().isRowHidden(cbr))
            self.assertFalse(window.archive_format.model().item(cbr).isEnabled())
        finally:
            window.close()
        for version, rar4 in (((7, 13), False), ((6, 24), True)):
            program = RarProgram("/opt/rar/rar", version)
            with self.subTest(version=version), \
                    patch("comic_stitcher.ui.export_screen.find_rar", return_value=program):
                window = ComicWindow()
                try:
                    self.assertFalse(window.archive_format.view().isRowHidden(cbr))
                    self.assertTrue(window.rar4.isHidden())
                    window.archive_format.setCurrentIndex(cbr)
                    self.assertFalse(window.rar4.isHidden())
                    self.assertEqual(window.rar4.isEnabled(), rar4)
                    window.rar4.setChecked(True)
                    opt = window.options_from_screen()
                    self.assertEqual((opt.archive_format, opt.rar_program, opt.rar4),
                                     ("cbr", "/opt/rar/rar", rar4))
                    window.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index("cbz"))
                    self.assertTrue(window.rar4.isHidden())
                    self.assertEqual(window.options_from_screen().rar_program, "")
                finally:
                    window.close()
        # CBR remembered, then RAR gone: back to CBZ.
        with patch("comic_stitcher.ui.export_screen.find_rar", return_value=None):
            window = ComicWindow()
        try:
            self.assertEqual(window.archive_format.currentIndex(), ARCHIVE_CHOICES.index("cbz"))
        finally:
            window.close()

    def test_page_notes_listed_marked_and_found_with_m(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i, number in enumerate((1, 2, 4, 5, 6, 7, 8)):  # 003 is missing.
                Image.new("RGB", (60, 80), (30 + i * 25, 20, 10)).save(folder / f"Book {number:03d}.png")
            window = ComicWindow()
            try:
                window.show(); window.scan_done(scan_manual_folder(folder))
                notice = window.page_notes_notice
                self.assertEqual(notice.text(), "One place in this comic is worth a look")
                self.assertIn("• Pages 2 and 3: File numbers jump from Book 002.png to Book 004.png: "
                              "a page may be missing between them.", notice.informativeText())
                self.assertIn("Press M there", notice.informativeText())
                self.assertNotIn("capitalisation", notice.informativeText())
                notice.close()
                window.enter_preview()
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 7)
                marked = [i for i, b in enumerate(window.thumb_buttons) if "ed3c9b" in b.styleSheet()]
                self.assertEqual(marked, [1, 2])
                self.assertIn("a page may be missing", window.thumb_buttons[1].toolTip())
                window.preview_index = 4
                window._review_key("marked")
                self.assertEqual(window.preview_index, 1)  # Round to the first.
                window._review_key("marked")
                self.assertEqual(window.preview_index, 2)
                self.assertIn("Marked page 2 of 2", window.statusBar().currentMessage())
            finally:
                window.close()
        # Twins' note explains their order.
        window = ComicWindow()
        self.addCleanup(window.close)
        window._show_page_notes([((0, 1), "Names differ only in capitalisation: comic - 001.jpg, comic - 001.JPG")])
        self.assertIn("lowercase first", window.page_notes_notice.informativeText())
        self.assertIn("If one is a duplicate, remove it in the final review", window.page_notes_notice.informativeText())
        self.assertIn("their names won't clash", window.page_notes_notice.informativeText())
        window.page_notes_notice.close()

    def test_off_center_art_is_measured_in_the_background_and_offered(self):
        from .test_centering import page
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i, (left, right) in enumerate(((200, 1550), (150, 1500), (175, 1525))):
                page(left, right, rng_seed=i).save(folder / f"{i + 1:03d}.png")
            window = ComicWindow()
            asked = []

            def answer(dialog):  # The pop-up offer: decline it the first time.
                asked.append(dialog.text())
                dialog.buttons()[[b.text() for b in dialog.buttons()].index("Leave it as it is")].click()
            try:
                window.show()
                window.skip_pair_review = True
                with patch.object(QMessageBox, "exec", answer):
                    window.scan_done(scan_manual_folder(folder))
                    self.wait_for(lambda: window.plan.centering_measured, seconds=60)
                    self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 3)
                    self.wait_for(lambda: not window.centering_row.isHidden() and asked)
                self.assertEqual(asked, ["The art on 2 of 3 pages sits off-center, probably because of "
                                         "print margins. Center it?"])
                self.assertTrue(window.plan.centering_offered)
                self.assertEqual(sorted(window.plan.centering), [0, 1])
                self.assertIn("The art on 2 of 3 pages sits off-center", window.centering_label.text())
                self.assertFalse(window.center_art_box.isChecked())
                self.assertTrue(window.center_page_box.isHidden())
                with patch.object(ComicWindow, "show_export") as export:
                    window.continue_to_export()  # Asked once only: on to export.
                export.assert_called_once()
                self.assertEqual(len(asked), 1)
                window.center_art_box.click()
                self.assertTrue(window.plan.center_art)
                window.preview_index = 2
                window._review_key("centered")  # C: round to the first moved page.
                self.assertEqual(window.preview_index, 0)
                self.assertIn("Off-center page 1 of 2", window.statusBar().currentMessage())
                self.assertFalse(window.center_page_box.isHidden())
                window.center_page_box.click()  # Leave this one as it was.
                self.assertEqual(window.plan.center_skipped, {0})
                window.undo_preview_removal()
                self.assertEqual(window.plan.center_skipped, set())
            finally:
                window.close()

    def test_continue_to_export_waits_for_the_centering_check(self):
        from PySide6.QtWidgets import QProgressDialog
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(3):
                Image.new("RGB", (60, 80), (30 + i * 25, 20, 10)).save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                with patch("comic_stitcher.ui.final_review.CenteringWorker"):
                    window.skip_pair_review = True
                    window.scan_done(scan_manual_folder(folder))
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 3)
                plan = window.plan
                running = Mock(isRunning=Mock(return_value=True))
                window.centering_checks = [running]
                waited = []

                def check_finishes(dialog):  # The result arrives while the dialog waits.
                    waited.append(dialog.labelText())
                    window._centering_done(plan, {1: (12, 0, 80)})
                    return dialog.result()

                def center(dialog):
                    dialog.buttons()[[b.text() for b in dialog.buttons()].index("Center the art")].click()
                with patch.object(QProgressDialog, "exec", check_finishes), \
                        patch.object(QMessageBox, "exec", center), \
                        patch.object(ComicWindow, "show_export") as export:
                    window.continue_to_export()
                self.assertEqual(waited, ["Checking whether the art is centered on each page…"])
                self.assertTrue(plan.center_art)
                export.assert_not_called()  # Centered just now: the pages are shown first.
                with patch.object(ComicWindow, "show_export") as export:
                    window.continue_to_export()
                export.assert_called_once()
                # Skipping the check goes on to export, without centering.
                window._stop_centering_checks = Mock()
                plan.centering_measured, plan.center_art, plan.centering_offered = False, False, False
                with patch.object(QProgressDialog, "exec", lambda dialog: 0), \
                        patch.object(ComicWindow, "show_export") as export:
                    window.continue_to_export()
                window._stop_centering_checks.assert_called_once()
                export.assert_called_once()
            finally:
                window.centering_checks = []
                window.close()

    def test_the_centering_result_shows_when_nothing_needs_it(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(3):
                Image.new("RGB", (60, 80), (30 + i * 25, 20, 10)).save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                with patch("comic_stitcher.ui.final_review.CenteringWorker"):
                    window.skip_pair_review = True
                    window.scan_done(scan_manual_folder(folder))
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 3)
                self.assertEqual(window.centering_label.text(),  # Still checking.
                                 "Checking whether the art is centered on each page…")
                self.assertFalse(window.centering_row.property("quiet"))
                window.plan.centering_left_alone = {"spread": 2, "already centered": 1}
                window._centering_done(window.plan, {})
                self.assertFalse(window.centering_row.isHidden())
                self.assertEqual(window.centering_label.text(),
                                 "Checked: the art is centered on every page (left alone: 2 spreads).")
                self.assertTrue(window.centering_row.property("quiet"))
                self.assertTrue(window.center_art_box.isHidden())
            finally:
                window.close()

    def test_dark_mode_colors_palette_and_icons(self):
        from PySide6.QtGui import QPalette
        from .ui import theme
        from .ui.common import interface_icon
        original = self.app.palette()
        theme.apply_theme(self.app, dark=True)
        try:
            self.assertTrue(theme.is_dark())
            palette = self.app.palette()
            # Check boxes: the box (Base) is dark and its tick (Text) light, unlike the blue highlight.
            self.assertLess(palette.color(QPalette.ColorRole.Base).lightness(), 70)
            self.assertGreater(palette.color(QPalette.ColorRole.Text).lightness(), 200)
            window = ComicWindow()
            try:
                self.assertIn(theme.TONES["drop"][1], window.import_zone.styleSheet())
                self.assertIn(theme.TONES["panel"][1], window.export_page.styleSheet())
                self.assertNotIn("#f9fbfd", window.export_page.styleSheet())
                self.assertIn(theme.TONES["control_hover"][1], window.next_preview_button.styleSheet())
            finally:
                window.close()
            # Line icons are drawn in the light text color.
            image = interface_icon("settings").pixmap(64, 64).toImage()
            inked = [image.pixelColor(x, y) for x in range(64) for y in range(64)
                     if image.pixelColor(x, y).alpha() > 200]
            self.assertTrue(inked)
            self.assertTrue(all(colour.lightness() > 200 for colour in inked))
        finally:
            theme.apply_theme(self.app, dark=False)
            self.app.setPalette(original)
        self.assertEqual(theme.tone("drop"), theme.TONES["drop"][0])

    def test_help_opens_the_guide_and_shortcuts_offline(self):
        from .ui.help import guide_path, shortcuts_markdown, slug
        self.assertTrue(guide_path().is_file(), guide_path())
        self.assertEqual(slug("Batches and the queue"), "batches-and-the-queue")
        self.assertEqual(slug("Input Formats & Import Options"), "input-formats--import-options")
        self.assertTrue(shortcuts_markdown(guide_path().read_text(encoding="utf-8")).startswith("## Keyboard"))
        window = ComicWindow()
        try:
            self.assertEqual([a.text() for a in window.help_button.menu().actions()],
                             ["User Guide\tF1", "Keyboard shortcuts\t?"])
            window.show_user_guide()
            guide = window.help_window
            self.assertEqual(guide.windowTitle(), "Comic Stitcher User Guide")
            text = guide.browser.toPlainText()
            self.assertIn("The steps at a glance", text)
            # Contents links lead to the headings, which carry GitHub's anchors.
            names = set()
            block = guide.browser.document().begin()
            while block.isValid():
                fragment = block.begin().fragment()
                if fragment.isValid():
                    names.update(fragment.charFormat().anchorNames())
                block = block.next()
            self.assertIn("keyboard-shortcuts", names)
            self.assertIn("centering-the-art", names)
            guide.find_box.setText("Center the art")
            guide.find_next()
            self.assertEqual(guide.browser.textCursor().selectedText(), "Center the art")
            guide.close()
            window.show_keyboard_shortcuts()
            shortcuts = window.shortcuts_window
            self.assertEqual(shortcuts.windowTitle(), "Keyboard shortcuts")
            self.assertIn("Next off-center page", shortcuts.browser.toPlainText())
            self.assertNotIn("The steps at a glance", shortcuts.browser.toPlainText())
            shortcuts.close()
        finally:
            window.close()

    def test_no_centering_check_when_turned_off(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(2):
                Image.new("RGB", (60, 80), (30 + i * 25, 20, 10)).save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:  # Closed while its pages still exist, so nothing loads them afterwards.
                window.settings.setValue("check_centering", False)
                window.skip_pair_review = True
                with patch("comic_stitcher.ui.final_review.CenteringWorker") as worker:
                    window.scan_done(scan_manual_folder(folder))
                worker.assert_not_called()
            finally:
                window.close()

    def test_sharp_yuv_option_is_per_size_and_needs_lossy_webp_and_cwebp(self):
        window = ComicWindow()
        self.addCleanup(window.close)
        with patch("comic_stitcher.ui.output_options.sharp_yuv_available", return_value=True):
            dialog = OutputOptionsDialog(ExportOptions("Book", format="webp"), window.custom_resize_values)
            try:
                full, reduced = dialog.advanced, dialog.reduced_advanced
                self.assertTrue(full.sharp_yuv.isEnabled())
                full.lossless.setChecked(True)
                self.assertFalse(full.sharp_yuv.isEnabled())  # Lossless WebP has nothing to sharpen.
                full.lossless.setChecked(False)
                reduced.sharp_yuv.setChecked(True)
                opt = ExportOptions("Book")
                dialog.apply_to(opt)
                self.assertFalse(opt.webp_sharp_yuv)
                self.assertTrue(opt.reduced_settings["webp_sharp_yuv"])
            finally:
                dialog.close()
        with patch("comic_stitcher.ui.output_options.sharp_yuv_available", return_value=False):
            dialog = OutputOptionsDialog(ExportOptions("Book", format="webp", webp_sharp_yuv=True),
                                         window.custom_resize_values)
            try:
                self.assertFalse(dialog.advanced.sharp_yuv.isEnabled())
                self.assertIn("cwebp", dialog.advanced.sharp_yuv.toolTip())
                opt = ExportOptions("Book")
                dialog.apply_to(opt)
                self.assertFalse(opt.webp_sharp_yuv)  # Never asks for a tool that isn't there.
            finally:
                dialog.close()

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
            self.assertEqual([b.text() for b in dialog.family_buttons.buttons()],
                             ["1988 × 3056", "2037 × 3056", "This page's own size"])
            self.assertFalse(any(b.isChecked() for b in dialog.family_buttons.buttons()))
            self.assertIsNone(dialog.choices[0])
            # Choosing a size settles a review page: its content is double-width.
            dialog._select_family(1988)
            self.assertEqual((dialog.choices[0].kind, dialog.choices[0].box), ("double", (112, 0, 4088, 3056)))
            dialog._select_family(2037)
            self.assertEqual(dialog.choices[0].box[2] - dialog.choices[0].box[0], 4074)
            dialog.choose("single")
            self.assertEqual(dialog.choices[0].box[2] - dialog.choices[0].box[0], 2037)
            self.assertTrue(dialog.family_buttons.buttons()[1].isChecked())
        finally:
            dialog.close()

    def test_one_page_size_for_all_pages_and_measuring_a_page_again(self):
        from .screenshot_geometry import CropProposal
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cover, clear, black = folder / "cover.png", folder / "clear.png", folder / "black.png"
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (115, 0, 305, 306))
                image.save(cover)
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("white", (111, 0, 309, 306))
                image.save(clear)
            with Image.new("RGB", (420, 306), "black") as image:
                image.paste("red", (150, 100, 270, 200))
                image.save(black)
            families = (195, 198)
            proposals = [
                CropProposal(cover, (420, 306), "single", (115, 0, 305, 306), (115, 0, 305, 306),
                             "measured", family_widths=families),
                CropProposal(clear, (420, 306), "single", (111, 0, 309, 306), (111, 0, 309, 306),
                             "measured", family_widths=families, family_width=198),
                CropProposal(folder / "spread.png", (420, 306), "double", (15, 0, 405, 306),
                             (15, 0, 405, 306), "measured", family_widths=families, family_width=195),
                CropProposal(black, (420, 306), "uncertain", None, (20, 0, 400, 306), "review",
                             family_widths=families),
            ]
            dialog = ScreenshotCropDialog(proposals)
            try:
                self.assertEqual(dialog.family_selection, {0: "own", 1: 198, 2: 195, 3: None})
                dialog.list.setCurrentRow(1)
                self.assertEqual([b.text() for b in dialog.family_buttons.buttons()][:2],
                                 ["195 × 306 (1 page)", "198 × 306 (1 page)"])
                dialog.all_pages.setChecked(True)
                self.assertEqual(dialog.book_family, ((420, 306), 198))
                self.assertEqual([c.box if c else None for c in dialog.choices],
                                 [(115, 0, 305, 306), (111, 0, 309, 306), (12, 0, 408, 306), (12, 0, 408, 306)])
                self.assertEqual(dialog.choices[3].kind, "double")
                self.assertTrue(dialog.choices[2].inferred)
                # With the box ticked, choosing another size on any page moves them all,
                # except the cover measured at its own size.
                dialog.list.setCurrentRow(2)
                dialog._select_family(195)
                self.assertEqual([c.box for c in dialog.choices],
                                 [(115, 0, 305, 306), (112, 0, 307, 306), (15, 0, 405, 306), (15, 0, 405, 306)])
                dialog.all_pages.setChecked(False)
                self.assertIsNone(dialog.book_family)
                # Measure a page again: it's cropped at the size it measures on its own.
                dialog.list.setCurrentRow(1)
                dialog._select_family("own")
                self.assertEqual((dialog.choices[1].box, dialog.family_selection[1]),
                                 ((111, 0, 309, 306), "own"))
                self.assertIn("(198 × 306)", dialog.family_buttons.buttons()[-1].text())
                # A page whose edges blend into the black can't be measured; nothing changes.
                dialog.list.setCurrentRow(3)
                before = dialog.choices[3]
                with patch.object(QMessageBox, "information") as told:
                    dialog._select_family("own")
                told.assert_called_once()
                self.assertEqual((dialog.choices[3], dialog.family_selection[3]), (before, 195))
            finally:
                dialog.close()

    def test_choosing_a_page_size_never_cuts_off_artwork(self):
        from .screenshot_geometry import CropProposal
        canvas, families = (4200, 3056), (1988, 2014)
        proposals = [
            CropProposal(Path("a.png"), canvas, "single", (1106, 0, 3094, 3056), (1106, 0, 3094, 3056),
                         "measured", family_widths=families, family_width=1988),
            CropProposal(Path("b.png"), canvas, "single", (1093, 0, 3107, 3056), (1093, 0, 3107, 3056),
                         "measured", family_widths=families, family_width=2014),
            # A black-bordered spread whose artwork is 4000 wide: only the 2014 double holds it.
            CropProposal(Path("c.png"), canvas, "double", (86, 0, 4114, 3056), (100, 0, 4100, 3056),
                         "suggested", family_widths=families, family_width=2014),
        ]
        dialog = ScreenshotCropDialog(proposals)
        try:
            dialog.list.setCurrentRow(0)
            with patch.object(QMessageBox, "information") as told:
                dialog.all_pages.setChecked(True)
            # The page measured at 2014 keeps it too: 1988 would cut its edges.
            self.assertIn("Screenshots 2, 3 kept their crop: the artwork is wider than a 1988 × 3056 page",
                          told.call_args.args[2])
            self.assertEqual([c.box for c in dialog.choices],
                             [(1106, 0, 3094, 3056), (1093, 0, 3107, 3056), (86, 0, 4114, 3056)])
            self.assertEqual(dialog.family_selection[2], 2014)
            dialog.list.setCurrentRow(2)
            with patch.object(QMessageBox, "information") as told:
                dialog._select_family(1988)
            told.assert_called_once()
            self.assertEqual((dialog.choices[2].box, dialog.family_selection[2]), ((86, 0, 4114, 3056), 2014))
        finally:
            dialog.close()

    def test_one_page_size_for_all_reaches_pages_that_needed_no_review(self):
        from .screenshot_geometry import CropProposal
        window = ComicWindow()
        try:
            window.screenshot_single_size = None
            families = (1909, 1988)
            proposals = [
                CropProposal(Path("cover.png"), (4200, 3056), "single", (1115, 0, 3084, 3056), None,
                             "measured", family_widths=families),
                CropProposal(Path("panels.png"), (4200, 3056), "double", (291, 0, 4109, 3056), None,
                             "measured", family_widths=families, family_width=1909),
                CropProposal(Path("review.png"), (4200, 3056), "uncertain", None, (200, 0, 4000, 3056),
                             "review", family_widths=families),
            ]
            choice = CropChoice(Path("review.png"), "double", (112, 0, 4088, 3056), True)
            with patch.object(window, "screenshot_crop_summary", return_value=False), \
                 patch("comic_stitcher.ui.screenshot_flow.ScreenshotCropDialog") as dialog, \
                 patch.object(window, "_stage_approved_screenshots") as stage:
                dialog.return_value.exec.return_value = QDialog.Accepted
                dialog.return_value.approved_choices.return_value = [choice]
                dialog.return_value.book_family = ((4200, 3056), 1988)
                dialog.return_value.canvas_families = {(4200, 3056): {1909, 1988}}
                window._screenshot_analysis_done(proposals)
                choices = stage.call_args.args[0]
                self.assertEqual([c.box for c in choices],
                                 [(1115, 0, 3084, 3056), (112, 0, 4088, 3056), (112, 0, 4088, 3056)])
                self.assertTrue(choices[1].inferred)
        finally:
            window.close()

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
                with patch("comic_stitcher.backend.classify_page",
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
                with patch("comic_stitcher.ui.import_flow.looks_like_screenshot_folder", return_value=(True, 1)), \
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
            with patch('comic_stitcher.gui.can_remember_key', return_value=True), \
                 patch('comic_stitcher.gui.protect_key', return_value='protected-test-value'), \
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
                        self.assertEqual(window.size(), window._compact_target_size())
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
                     patch("comic_stitcher.ui.import_flow.check_pdf_jpegs", return_value=(None, "Page 1: the JPEG is CMYK.")):
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
            self.assertGreaterEqual(window.height(), START_SIZE.height())
        finally:
            window.close()

    def test_finished_export_buttons_leave_with_the_next_job(self):
        # Eric: after an export, measuring a second screenshot folder still showed
        # "Process another comic", which went back to the start screen while the
        # measuring went on unseen.
        window = ComicWindow()
        try:
            window.show()
            window.pages.setCurrentWidget(window.progress_page)
            window.done_button.show(); window.repeat_button.show()  # A finished export.
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(window, "_run_worker") as run:
                window._start_screenshot_import(Path(directory))
                run.assert_called_once()
                self.assertEqual(window.progress_label.text(), "Measuring screenshots…")
                self.assertTrue(window.done_button.isHidden())
                self.assertTrue(window.repeat_button.isHidden())
            # And while work runs, the buttons do nothing even if reached.
            with patch.object(window, "worker") as worker:
                worker.isRunning.return_value = True
                window.pages.setCurrentWidget(window.progress_page)
                window.new_comic()
                self.assertIs(window.pages.currentWidget(), window.progress_page)
        finally:
            window.close()

    def test_interface_icons_load(self):
        # The icons come from a compiled Qt resource imported by ui/common.py; a
        # wrong import path there would quietly leave every button without its icon.
        from .ui import common
        self.assertIsNotNone(common.icons_resource)
        for name in ("settings", "left-caret", "right-caret"):
            self.assertFalse(common.interface_icon(name).isNull(), name)
        window = ComicWindow()
        try:
            self.assertFalse(window.settings_button.icon().isNull())
        finally:
            window.close()

    def test_queue_drag_shows_the_pink_line_at_every_position(self):
        # Eric: Qt's black line was thick between comics, thin below the last and
        # missing above the first on Windows, though a drop there worked.
        from .ui.thumbnails import QueueList
        listing = QueueList()
        try:
            for name in ("One", "Two", "Three"):
                listing.addItem(name)
            listing.resize(300, 200); listing.show(); self.app.processEvents()
            self.assertFalse(listing.showDropIndicator())
            rects = [listing.visualItemRect(listing.item(i)) for i in range(3)]
            self.assertEqual(listing.boundary_at(rects[0].top() + 1), 0)
            self.assertEqual(listing.boundary_at(rects[1].bottom() - 1), 2)
            self.assertEqual(listing.boundary_at(rects[2].bottom() + 20), 3)
            for boundary, expected in ((0, rects[0].top()), (1, (rects[0].bottom() + 1 + rects[1].top()) // 2),
                                       (3, rects[2].bottom() + 1)):
                listing.show_drop(boundary)
                self.assertFalse(listing.drop_marker.isHidden())
                self.assertEqual(listing.drop_marker.width(), listing.viewport().width())
                self.assertLessEqual(abs(listing.drop_marker.geometry().center().y() - max(3, expected)), 1)
                self.assertGreaterEqual(listing.drop_marker.y(), 0)  # Visible even above the first comic.
            listing.clear_drop()
            self.assertTrue(listing.drop_marker.isHidden())
            moved = []
            listing.reordered.connect(lambda: moved.append(True))
            listing.move_to_boundary(2, 0)  # The last comic to the top.
            self.assertEqual([listing.item(i).text() for i in range(3)], ["Three", "One", "Two"])
            listing.move_to_boundary(0, 3)  # And to the bottom.
            self.assertEqual([listing.item(i).text() for i in range(3)], ["One", "Two", "Three"])
            self.assertEqual(listing.currentRow(), 2)
            listing.move_to_boundary(1, 2)  # Just below itself: stays put.
            self.assertEqual(len(moved), 2)
        finally:
            listing.close()

    def test_final_review_is_drawn_when_a_resumed_review_goes_back_to_it(self):
        # Eric: a saved review resumed on Create your comic; Back to Comic Preview
        # showed "Preparing comic preview…" for good and an empty thumbnail strip.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(5):
                Image.new("RGB", (60, 80), (30 + i * 40, 20, 10)).save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_manual_folder(folder))
                # As resuming at the export stage does: the final review, then straight on.
                window.enter_preview()
                window.show_export()
                self.app.processEvents()
                self.assertFalse(window.preview_ready)
                window.return_preview_from_export()
                self.wait_for(lambda: len(getattr(window, "thumb_buttons", [])) == 5)
                self.assertTrue(window.preview_ready)
                self.assertNotEqual(window.preview_heading.text(), "Preparing comic preview…")
            finally:
                window.close()

    def test_compact_windows_are_as_large_as_their_contents(self):
        from PySide6.QtWidgets import QPushButton
        window = ComicWindow()
        try:
            window.show(); self.app.processEvents()
            # Start: no heading (the title bar names the program), the gear with
            # the queue buttons, and no queue text.
            self.assertFalse([label for label in window.start_page.findChildren(QLabel)
                              if label.text() == "Comic Stitcher"])
            self.assertFalse(hasattr(window, "queue_status"))
            self.assertLess(abs(window.settings_button.geometry().center().y() -
                                window.queue_view_button.geometry().center().y()), 6)
            self.assertLess(window.settings_button.x(), window.queue_view_button.x())
            # Progress: just the messages, bar and caption.
            window.progress_source.setText("Avengers By Jed MacKay v06 - The Grail.pdf")
            window.progress_label.setText("Rendering PDF pages: 64 of 172…")
            window.pages.setCurrentWidget(window.progress_page)
            window._compact_window(); self.app.processEvents()
            needed = window.progress_page.minimumSizeHint().height()
            self.assertLessEqual(window.pages.height(), needed + 4)
            # Create your comic can't be dragged narrower than its fields.
            window.pages.setCurrentWidget(window.export_page)
            window._compact_window(); self.app.processEvents()
            window.resize(300, window.height()); self.app.processEvents()
            book = window.book_scroll.widget()
            self.assertGreaterEqual(window.book_scroll.viewport().width(), book.minimumSizeHint().width())
        finally:
            window.close()
        # Settings: as tall as its contents, no gaps between them.
        def check(dialog):
            layout = dialog.layout()
            self.assertLessEqual(dialog.height(), layout.totalHeightForWidth(dialog.width()) + 2)
            return QDialog.Rejected
        window = ComicWindow()
        try:
            with patch.object(OutlinedDialog, "exec", check):
                window.show_settings()
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
                # Reaching this screen queues the comic; one blue button moves on.
                self.assertEqual(len(window.queue_jobs), 1)
                self.assertEqual(window.create_button.text(), "Review next comic")
                window.pending_imports.clear()
                window.refresh_export()
                self.assertEqual(window.create_button.text(), "Start export")
                earlier = QueuedComic(BookPlan(scan_manual_folder(folder)), ExportOptions("Earlier", workers=1),
                                      None, None, None)
                window.queue_jobs.insert(0, earlier)
                with patch.object(window, "start_queue") as start:
                    window.create_button.click()
                    start.assert_called_once()
                self.assertEqual(len(window.queue_jobs), 2)
                self.assertIsNone(window.plan)
                self.assertIsNone(window.current_job)
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
                window.refresh_export()
                self.assertEqual(window.create_button.text(), "Review next comic")
                with patch.object(window, "_prepare_import") as prepare:
                    window.create_button.click()
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
            with patch("comic_stitcher.ui.queue.import_pdf", return_value=scan_manual_folder(pages)), patch(
                    "comic_stitcher.ui.queue.export_book") as exporter:
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
                with patch("comic_stitcher.ui.queue_flow.clear_session", side_effect=PermissionError("locked")), patch(
                        "comic_stitcher.gui.diagnostics.exception") as logged:
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
                    with patch("comic_stitcher.gui.shutil.rmtree", side_effect=PermissionError("locked crops")), patch(
                            "comic_stitcher.gui.QTimer.singleShot") as advance, patch(
                            "comic_stitcher.ui.queue_flow.queued_outputs", return_value=[destination]):
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
                window._queue_window_action("another")
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
                    window.show_queue()
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(window.queue_jobs)
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_queue_order_can_be_changed_by_dragging_or_keyboard(self):
        from PySide6.QtWidgets import QListWidget, QPushButton
        from .gui import QueueList
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (40, 60), "white").save(root / "001.png")
            window = ComicWindow()
            try:
                for name in ("First", "Second", "Third"):
                    window.queue_jobs.append(QueuedComic(BookPlan(scan_manual_folder(root)),
                                                         ExportOptions(name, workers=1), None, None, None))
                names = lambda: [job.options.book_name for job in window.queue_jobs]
                def reorder(dialog):
                    listing = dialog.findChild(QueueList)
                    self.assertEqual(listing.dragDropMode(), QListWidget.InternalMove)
                    # Keyboard: Ctrl+End sends the selected comic to the bottom, Ctrl+Up moves it up one.
                    listing.setCurrentRow(0)
                    QTest.keyClick(listing, Qt.Key_End, Qt.ControlModifier)
                    self.assertEqual(names(), ["Second", "Third", "First"])
                    QTest.keyClick(listing, Qt.Key_Up, Qt.ControlModifier)
                    self.assertEqual(names(), ["Second", "First", "Third"])
                    # A drag ends with the list in its new order; the queue follows it.
                    listing.insertItem(0, listing.takeItem(2))
                    listing.reordered.emit()
                    self.assertEqual(names(), ["Third", "Second", "First"])
                    self.assertTrue(listing.item(0).text().startswith("Third"))
                    # Removing still removes the comic shown in that row.
                    listing.setCurrentRow(1)
                    {b.text(): b for b in dialog.findChildren(QPushButton)}["Remove selected"].click()
                    self.assertEqual(names(), ["Third", "First"])
                    return QDialog.Rejected
                with patch.object(QDialog, "exec", reorder):
                    window.show_queue()
                self.assertEqual(names(), ["Third", "First"])
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_queue_can_be_viewed_from_the_export_screen_while_reviewing(self):
        from PySide6.QtWidgets import QPushButton
        from .gui import QueueList
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Image.new("RGB", (40, 60), "white").save(root / "001.png")
            window = ComicWindow()
            try:
                self.assertTrue(window.export_queue_button.isHidden())  # Nothing queued yet.
                window.queue_jobs.append(QueuedComic(BookPlan(scan_manual_folder(root)),
                                                     ExportOptions("Queued", workers=1), None, None, None))
                window._refresh_queue_status()
                self.assertFalse(window.export_queue_button.isHidden())
                self.assertEqual(window.export_queue_button.text(), "View queue (1)…")
                def check(dialog):
                    self.assertEqual(dialog.findChild(QueueList).count(), 1)
                    buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
                    for name in ("Start export", "Add comic files…", "Add folder…"):
                        self.assertTrue(buttons[name].isHidden(), name)
                    self.assertFalse(buttons["Add another comic"].isHidden())
                    return QDialog.Rejected
                with patch.object(QDialog, "exec", check):
                    window.export_queue_button.click()
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_buttons_and_drop_downs_share_their_heights(self):
        """Plain buttons are all the standard height, wherever they are, including
        message boxes opened from the queue; blue buttons all share one taller
        height; drop-down lists match on every screen."""
        from PySide6.QtWidgets import QComboBox, QPushButton
        from .gui import QueueList
        standard = QPushButton("Remove selected").sizeHint().height()
        plain, blue, combos, details = {}, {}, {}, {}

        def measure(where, widget):
            widget.show()
            self.app.processEvents()
            for button in widget.findChildren(QPushButton):
                if button.isVisible() and button.text():
                    (blue if "#1976cf" in button.styleSheet() else plain)[f"{where}: {button.text()}"] = button.height()
            for combo in widget.findChildren(QComboBox):
                if combo.isVisible():
                    combos[f"{where}: {combo.currentText()}"] = combo.height()
                    details[f"{where}: {combo.currentText()}"] = (combo.height(), combo.minimumHeight(),
                        combo.maximumHeight(), combo.sizeHint().height(), combo.testAttribute(Qt.WA_WState_Polished))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(self.make_book(root, "book")))
                window.show_export()
                window.queue_jobs.append(QueuedComic(BookPlan(scan_manual_folder(self.make_book(root, "two"))),
                                                     ExportOptions("Two", workers=1), None, None, None))
                window._refresh_queue_status()
                measure("export", window)

                def check(dialog):
                    measure("queue", dialog)
                    box = QMessageBox(QMessageBox.Question, "Remove this comic?", "Test",
                                      QMessageBox.Yes | QMessageBox.No, dialog)
                    measure("box", box)
                    box.close()
                    return QDialog.Rejected
                with patch.object(QDialog, "exec", check):
                    window.export_queue_button.click()
                choice = ImportChoiceDialog(root / "book", folder=True)
                measure("import", choice)
                choice.close()
                options = OutputOptionsDialog(ExportOptions("Two"), window.custom_resize_values, window)
                measure("options", options)
                options.reject()
            finally:
                window.queue_jobs.clear()
                window.close()
        print("standard", standard, "plain", plain, "blue", blue, "drop-downs", combos)
        self.assertTrue(any(k.startswith("box") for k in plain), plain)
        self.assertTrue(any(k.startswith("queue") for k in plain), plain)
        self.assertEqual(set(plain.values()), {standard}, plain)
        self.assertTrue(any(k.startswith("import") for k in blue), blue)
        self.assertTrue(any(k.startswith("export") for k in blue), blue)
        self.assertEqual(len(set(blue.values())), 1, blue)
        self.assertGreater(next(iter(blue.values())), standard)
        for where in ("export", "import", "options"):
            self.assertTrue(any(k.startswith(where) for k in combos), (where, combos))
        self.assertEqual(len(set(combos.values())), 1, (getattr(self.app, '_drop_down_height', None) and self.app._drop_down_height.height, details))

    def test_dialog_buttons_use_the_windows_order_everywhere(self):
        """Linux desktops reorder dialog buttons (Cancel lands in the middle);
        the app keeps the Windows order on every system."""
        from PySide6.QtWidgets import QPushButton
        box = QMessageBox(QMessageBox.Question, "Temporary storage for this batch", "Test")
        box.addButton("Clear temporary pages", QMessageBox.AcceptRole)
        box.addButton("Keep for faster export", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        yes_no = QMessageBox(QMessageBox.Question, "Remove this comic?", "Test", QMessageBox.Yes | QMessageBox.No)
        try:
            orders = []
            for dialog in (box, yes_no):
                dialog.show()
                self.app.processEvents()
                buttons = [b for b in dialog.findChildren(QPushButton) if b.isVisible()]
                orders.append([b.text().replace("&", "") for b in sorted(buttons, key=lambda b: b.mapToGlobal(b.rect().topLeft()).x())])
            self.assertEqual(orders, [["Clear temporary pages", "Keep for faster export", "Cancel"], ["Yes", "No"]])
        finally:
            box.close()
            yes_no.close()

    def test_no_pair_review_goes_straight_to_the_final_review(self):
        from .ui.import_dialogs import NO_PAIR_REVIEW
        with tempfile.TemporaryDirectory() as directory:
            folder = self.make_book(Path(directory), "complete")
            Image.new("RGB", (60, 80), "white").save(folder / "002.png")
            window = ComicWindow()
            try:
                window.show()
                window.review_mode.setCurrentIndex(NO_PAIR_REVIEW)
                with patch("comic_stitcher.backend.classify_page",
                           side_effect=AssertionError("no pair review ran the detector")):
                    window.start_scan(folder)
                    self.wait_for(lambda: window.plan is not None)
                    self.wait_for(lambda: not window.worker.isRunning())
                self.assertIs(window.pages.currentWidget(), window.preview)
                self.assertIsNone(window.plan.current())
                self.assertEqual(len(window.plan.entries()), 3)  # Nothing joined.
                self.assertFalse(window.plan.approved())
                window.new_comic()
                self.assertEqual(window.review_mode.currentIndex(), 0)  # Not kept for the next comic.
                self.assertFalse(window.skip_pair_review)
            finally:
                window.close()

    def test_import_dialog_offers_no_pair_review(self):
        from .ui.import_dialogs import NO_PAIR_REVIEW
        dialog = ImportChoiceDialog(Path("book.cbr"), folder=False)
        try:
            self.assertEqual(dialog.review_mode.itemText(NO_PAIR_REVIEW), "None — the pages are already complete")
        finally:
            dialog.close()

    def test_lossless_webp_is_suggested_when_far_smaller(self):
        with tempfile.TemporaryDirectory() as directory:
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(self.make_book(Path(directory), "strips")))
                window.show_export()
                window.opt.format, window.opt.webp_lossless = "jpeg", False
                generation = window.estimate_generation
                window._size_estimate_ready(generation, {"full": 280_000_000})
                window._lossless_estimate_ready(generation, 250_000_000)  # Not much smaller: quiet.
                self.assertTrue(window.lossless_hint_row.isHidden())
                window._lossless_estimate_ready(generation, 31_000_000)
                self.assertFalse(window.lossless_hint_row.isHidden())
                self.assertIn("lossless WebP", window.lossless_hint.text())
                self.assertIn("no loss of quality", window.lossless_hint.text())
                window._lossless_estimate_ready(generation - 1, 31_000_000)  # Out of date: ignored.
                usual = {"format": "jpeg", "spread_format": "jpeg", "webp_lossless": False}
                with patch.object(ComicWindow, "_saved_formats", return_value=usual), \
                        patch.object(window.settings, "setValue") as saved:
                    window.lossless_button.click()
                    self.assertEqual((window.opt.format, window.opt.webp_lossless), ("webp", True))
                    self.assertTrue(window.lossless_hint_row.isHidden())  # A new estimate starts.
                    self.assertEqual(window.options_from_screen().format, "webp")
                    self.assertEqual(window.current_job.options.format, "webp")  # The queued entry too.
                    self.assertFalse([c for c in saved.call_args_list if c.args[0] == "output_format"])
                    # Saving through Edit options keeps the usual format, not this comic's.
                    window._save_output_preferences()
                    values = {c.args[0]: c.args[1] for c in saved.call_args_list}
                    self.assertEqual((values["output_format"], values["webp_lossless"]), ("jpeg", False))
                    self.assertEqual(window.opt.format, "webp")
                    window.new_comic()  # The next comic starts with the usual format again.
                    self.assertEqual((window.usual_opt.format, window.usual_opt.webp_lossless), ("jpeg", False))
                    self.assertFalse(window.lossless_for_this_comic)
            finally:
                window.opt.format, window.opt.webp_lossless = "jpeg", False
                window.queue_jobs.clear()
                window.close()

    def test_quick_check_runs_during_review_and_shows_the_note_early(self):
        with tempfile.TemporaryDirectory() as directory:
            window = ComicWindow()
            try:
                window.opt.format, window.opt.spread_format, window.opt.webp_lossless = "jpeg", "jpeg", False
                window.scan_done(scan_manual_folder(self.make_book(Path(directory), "strips")))
                self.wait_for(lambda: window.lossless_checked[0] is window.plan)  # Started by the import.
                self.wait_for(lambda: not window.lossless_checks)
                # A book the check favours: the note is there as the screen opens.
                key = window.lossless_checked[1]
                self.assertIsNotNone(key)
                window.lossless_checked = (window.plan, key, 0.2)
                requests = []
                with patch.object(window.size_worker, "request",
                                  side_effect=lambda *args, **kw: requests.append(kw)):
                    window.show_export()
                    self.assertFalse(window.lossless_hint_row.isHidden())
                    self.assertIn("would likely make this comic much smaller", window.lossless_hint.text())
                    window._start_size_estimate()
                    self.assertTrue(requests[-1]["compare_lossless"])
                    # The full estimate disagrees: the note goes.
                    generation = window.estimate_generation
                    window._size_estimate_ready(generation, {"full": 100_000_000})
                    window._lossless_estimate_ready(generation, 90_000_000)
                    self.assertTrue(window.lossless_hint_row.isHidden())
                    # A book the check rules out: no note, and no full comparison.
                    window.lossless_checked = (window.plan, key, 1.6)
                    window._schedule_size_estimate()
                    self.assertTrue(window.lossless_hint_row.isHidden())
                    window._start_size_estimate()
                    self.assertFalse(requests[-1]["compare_lossless"])
                    # Eric: the check ruled lossless out against the usual JPEG, then
                    # WebP at 95 was chosen (168 MB, lossless 37 MB). Its answer no
                    # longer applies, so the full comparison runs.
                    window.opt.format, window.opt.webp_quality = "webp", 95
                    self.assertIsNone(window._lossless_share())
                    window._start_size_estimate()
                    self.assertTrue(requests[-1]["compare_lossless"])
                    generation = window.estimate_generation
                    window._size_estimate_ready(generation, {"full": 168_000_000})
                    window._lossless_estimate_ready(generation, 37_000_000)
                    self.assertFalse(window.lossless_hint_row.isHidden())
                    from .ui.common import readable_size  # MB as this system's file manager counts them.
                    self.assertIn(f"about {readable_size(37_000_000)} as lossless WebP", window.lossless_hint.text())
                    # Back to the settings it measured: its answer applies again.
                    window.opt.format, window.opt.webp_quality = "jpeg", 90
                    self.assertEqual(window._lossless_share(), 1.6)
                window.new_comic()
                self.assertEqual(window.lossless_checked, (None, None, None))
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_a_spread_with_repeated_art_is_lined_up_from_seam(self):
        import numpy as np
        from PySide6.QtCore import QPoint
        from .gui import OverlapDialog
        from .test_spread_overlap import artwork, halves
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "book"
            folder.mkdir()
            art = artwork()
            left, right = halves(art, 30)
            Image.new("RGB", left.size, "white").save(folder / "001.png")
            left.save(folder / "002.png")
            right.save(folder / "003.png")
            window = ComicWindow()
            try:
                window.show()
                window.skip_pair_review = True
                window.scan_done(scan_manual_folder(folder))
                window.plan.join(1, override_boundary=True)
                window.rebuild_thumbnails()
                window.preview_index = 1
                window.show_preview()
                self.assertTrue(window.preview_seam_button.isEnabled())  # One spread: line it up.
                seen = {}

                def line_up(dialog):
                    self.assertIsInstance(dialog, OverlapDialog)
                    seen["found"] = dialog.pixels.value()  # Measured as the window opens.
                    self.assertIn("30 px", dialog.status.text())
                    self.assertEqual(dialog.seam_image().width, 2 * dialog.strip)
                    dialog.together.click()
                    dialog.apart.click()
                    self.assertEqual(dialog.top_switch.side, "left")  # The default.
                    before = dialog.seam_image()
                    QTest.mouseClick(dialog.top_switch, Qt.LeftButton)
                    self.assertEqual((dialog.overlap(), dialog.top_switch.side), ((30, "right"), "right"))
                    after = np.asarray(dialog.seam_image())
                    self.assertEqual(after.shape, np.asarray(before).shape)
                    # The view doesn't slide: only the overlapping columns change.
                    edge = dialog.strip - 15 - 1
                    self.assertTrue(np.array_equal(after[:, :edge], np.asarray(before)[:, :edge]))
                    self.assertTrue(np.array_equal(after[:, -edge:], np.asarray(before)[:, -edge:]))
                    QTest.mouseClick(dialog.top_labels["left"], Qt.LeftButton)
                    self.assertEqual(dialog.overlap(), (30, "left"))
                    QTest.mouseClick(dialog.image, Qt.LeftButton, pos=QPoint(dialog.image.width() - 5, 5))
                    self.assertEqual(dialog.overlap(), (30, "right"))
                    QTest.mouseClick(dialog.image, Qt.LeftButton, pos=QPoint(5, 5))  # The left half.
                    return QDialog.Accepted
                with patch.object(QDialog, "exec", line_up):
                    window.preview_seam_button.click()
                self.assertEqual(seen["found"], 30)
                self.assertEqual(window.plan.join_overlaps, {1: (30, "left")})
                window.undo_preview_removal()
                self.assertEqual(window.plan.join_overlaps, {})
            finally:
                window.close()

    def test_overlapped_halves_are_suggested_lined_up_in_pair_review(self):
        from .test_spread_overlap import SuggestionTests
        with tempfile.TemporaryDirectory() as directory:
            scan = SuggestionTests().book(Path(directory))
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan)
                while window.plan.current() and window.plan.current()[1].left != 1:
                    window.decide(False)
                self.assertIn("overlapping halves (34 px", window.review_heading.text())
                self.assertEqual(window.joined_review.overlap, 34)
                rects = window.joined_review.image_rects()
                if rects:  # Drawn overlapping, as the spread will be joined.
                    left, right = rects
                    self.assertLess(right.left(), left.right())
                window.decide(True)
                self.assertEqual(window.plan.join_overlaps, {1: (34, "left")})
            finally:
                window.close()

    def test_find_overlap_covers_a_thin_border(self):
        import numpy as np
        from .gui import OverlapDialog
        from .test_spread_overlap import artwork
        art = artwork(width=2000, seed=19)
        left = np.full((700, 1003, 3), 255, np.uint8)
        left[:, :1000] = np.asarray(art)[:, :1000]
        dialog = OverlapDialog(Image.fromarray(left), art.crop((1000, 0, 2000, 700)), (0, "left"))
        try:
            self.assertEqual(dialog.overlap(), (3, "right"))
            self.assertIn("3 px border on the left page", dialog.status.text())
            self.assertEqual(dialog.top_switch.side, "right")
        finally:
            dialog.close()

    def make_book(self, root: Path, name: str, shade=0) -> Path:
        folder = root / name
        folder.mkdir()
        for page in range(2):
            Image.new("RGB", (60, 80), (shade, page * 90, 40)).save(folder / f"{page:03d}.png")
        return folder

    def test_reaching_the_export_screen_queues_the_comic_and_follows_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(self.make_book(root, "book")))
                self.assertFalse(window.queue_jobs)
                window.show_export()
                self.assertEqual(len(window.queue_jobs), 1)
                job = window.queue_jobs[0]
                self.assertIs(job, window.current_job)
                window.book_name.setText("Renamed on screen")
                self.assertEqual(job.options.book_name, "Renamed on screen")
                self.assertEqual(window.export_queue_button.text(), "View queue (1)…")
                # Back in the preview, the comic stays queued; returning doesn't add it twice.
                window.return_preview_from_export()
                window.show_export()
                self.assertEqual(window.queue_jobs, [job])
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_a_single_comic_exports_directly_and_can_be_exported_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = ComicWindow()
            try:
                window.show()
                window.settings.setValue("workers", 1)
                window.scan_done(scan_manual_folder(self.make_book(root, "book")))
                window.show_export()
                window.book_name.setText("Only One")
                self.assertEqual(window.create_button.text(), "Start export")
                window.create_button.click()
                self.wait_for(lambda: not window.repeat_button.isHidden(), seconds=20)
                self.assertTrue((root / "Only One.cbz").is_file())
                self.assertFalse(window.queue_jobs)
                window.repeat_button.click()  # The comic and its pages are still at hand.
                self.assertIs(window.pages.currentWidget(), window.export_page)
                self.assertEqual(len(window.queue_jobs), 1)
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_double_click_reopens_a_queued_comics_settings(self):
        from .gui import QueueList
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = ComicWindow()
            try:
                window.show()
                for index, fmt in ((1, "png"), (2, "jpeg")):
                    window.scan_done(scan_manual_folder(self.make_book(root, f"book-{index}", index * 60)))
                    window.show_export()
                    window.opt.format = fmt
                    window.book_name.setText(f"Book {index}")
                    window._queue_window_action("another")
                first, second = window.queue_jobs
                self.assertEqual(first.options.format, "png")
                # On the third comic's screen, open the first comic's settings from the queue.
                window.scan_done(scan_manual_folder(self.make_book(root, "book-3", 200)))
                window.show_export()
                window.book_name.setText("Book 3")
                third = window.current_job
                def open_first(dialog):
                    listing = dialog.findChild(QueueList)
                    self.assertEqual(listing.count(), 3)
                    listing.itemDoubleClicked.emit(listing.item(0))
                    return QDialog.Accepted
                with patch.object(QDialog, "exec", open_first):
                    window.export_queue_button.click()
                self.assertIs(window.current_job, first)
                self.assertTrue(window.editing_queued)
                self.assertTrue(window.export_back_button.isHidden())  # Pairs and pages stay as reviewed.
                self.assertEqual(window.book_name.text(), "Book 1")
                self.assertEqual(window.options_from_screen().format, "png")
                self.assertEqual(third.options.book_name, "Book 3")  # Still queued as left.
                window.opt.format = "jpeg"
                window.refresh_export()
                self.assertEqual(first.options.format, "jpeg")
                with patch.object(window, "start_queue") as start:
                    window.create_button.click()
                    start.assert_called_once()
                self.assertEqual([job.options.book_name for job in window.queue_jobs], ["Book 1", "Book 2", "Book 3"])
                self.assertEqual(first.options.format, "jpeg")
                self.assertFalse(window.editing_queued)
            finally:
                window.queue_jobs.clear()
                window.close()

    def test_removing_the_comic_on_screen_skips_it(self):
        from .gui import QueueList
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(self.make_book(root, "book")))
                window.show_export()
                def remove_it(dialog):
                    listing = dialog.findChild(QueueList)
                    listing.setCurrentRow(0)
                    {b.text(): b for b in dialog.findChildren(QPushButton)}["Remove selected"].click()
                    return QDialog.Accepted
                from PySide6.QtWidgets import QPushButton
                with patch.object(QDialog, "exec", remove_it), \
                        patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.export_queue_button.click()
                self.assertFalse(window.queue_jobs)
                self.assertIsNone(window.plan)
                self.assertEqual(window.pages.currentIndex(), 0)
            finally:
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
                window.settings.setValue("workers", 1)
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
                    window._queue_window_action("another")
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
                window.settings.setValue("workers", 1)
                window._queue_window_action("another")
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
            with patch("comic_stitcher.ui.queue.import_pdf", return_value=scan) as importer, \
                 patch("comic_stitcher.ui.queue.export_book", side_effect=lambda p, o, **kw: captured.append(p)):
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
                window._queue_window_action("another")
                (folder / "000.png").unlink()
                shown = []
                with patch.object(QMessageBox, "exec", lambda dialog: shown.append(dialog.text())):
                    window.start_queue()
                    self.wait_for(lambda: window.active_queue_job is None and
                                  window.pages.currentIndex() == 0 and
                                  window.queue_start_button.isEnabled(), seconds=10)
                self.assertEqual(len(window.queue_jobs), 1)
                self.assertIn("Retry Me", shown[0])
            finally:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.close()

    def test_low_disk_space_is_warned_before_exporting(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(2):
                Image.new("RGB", (60, 80), "white").save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                window.show_export()
                shown = []

                def cancel(dialog):
                    shown.append(dialog.text())
                    dialog.button(QMessageBox.Cancel).click()
                with patch("comic_stitcher.gui.shortfall", return_value=10 * 2 ** 20), \
                        patch.object(QMessageBox, "exec", cancel), \
                        patch.object(ComicWindow, "_run_worker") as run:
                    window.start_export()
                run.assert_not_called()  # Cancelled: nothing started.
                self.assertIn("Creating this comic needs about", shown[0])
                self.assertIn("free", shown[0])
            finally:
                window.close()

    def test_another_folder_is_offered_when_the_usual_one_cannot_be_written(self):
        from .problems import Problem
        from PySide6.QtWidgets import QFileDialog
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as elsewhere:
            folder = Path(directory) / "Book"
            folder.mkdir()
            for i in range(2):
                Image.new("RGB", (60, 80), "white").save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder))
                window.show_export()
                answers = iter([Problem("Comic Stitcher is unable to save to this location.", "access"), None])
                shown = []

                def choose(dialog):
                    shown.append(dialog.text())
                    next(b for b in dialog.buttons() if b.text() == "Choose folder…").click()
                with patch("comic_stitcher.ui.export_screen.cannot_write", side_effect=lambda f: next(answers)), \
                        patch.object(QMessageBox, "exec", choose), \
                        patch.object(QFileDialog, "getExistingDirectory", return_value=elsewhere), \
                        patch.object(ComicWindow, "_run_worker") as run:
                    window.start_export()
                self.assertIn("can't be saved in the same folder as the original", shown[0])
                self.assertEqual(window.plan.scan.output_folder, Path(elsewhere))
                run.assert_called_once()
                # Cancelled: nothing starts.
                with patch("comic_stitcher.ui.export_screen.cannot_write",
                           return_value=Problem("Not allowed.", "access")), \
                        patch.object(QMessageBox, "exec", lambda dialog: dialog.button(QMessageBox.Cancel).click()), \
                        patch.object(ComicWindow, "_run_worker") as run:
                    window.start_export()
                run.assert_not_called()
            finally:
                window.close()

    def test_queue_renames_duplicate_output_before_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            Image.new("RGB", (60, 80), "white").save(folder / "000.png")
            window = ComicWindow()
            try:
                for attempt in range(2):
                    window.scan_done(scan_manual_folder(folder))
                    window.show_export()
                    window.book_name.setText("Same Name")
                    if attempt == 0:
                        window._queue_window_action("another")
                    else:
                        outputs = window._preflight_export(window.options_from_screen())
                        self.assertEqual(window.book_name.text(), "Same Name - New export")
                        self.assertEqual(outputs[0].name, "Same Name - New export.cbz")
                        self.assertIn("already exists or is queued", window.export_name_notice)
                        break
            finally:
                with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
                    window.close()

    def test_cover_and_comicinfo_files_beside_the_comic(self):
        from .gui import ARCHIVE_CHOICES
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "pages"
            folder.mkdir()
            for i in range(2):
                Image.new("RGB", (60, 80), (40 * i, 20, 10)).save(folder / f"{i:03d}.png")
            window = ComicWindow()
            try:
                window.show()
                window.scan_done(scan_manual_folder(folder)); window.show_export()
                window.book_name.setText("Book")
                window.opt.format = "jpeg"
                window.save_cover.setChecked(True)
                window.include_comicinfo.setChecked(False)
                window.refresh_export()
                self.assertTrue(window.save_comicinfo.isHidden())  # Only with Include ComicInfo.xml.
                window.include_comicinfo.setChecked(True)
                window.save_comicinfo.setChecked(True)
                self.assertFalse(window.save_comicinfo.isHidden())
                self.assertTrue(window.save_comicinfo.isEnabled())
                self.assertEqual([p.name for p in window._preflight_export(window.options_from_screen())],
                                 ["Book.cbz", "Book - Cover.jpg", "Book - ComicInfo.xml"])
                window.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index("none"))
                self.assertFalse(window.save_comicinfo.isEnabled())  # The page folder holds it.
                self.assertEqual([p.name for p in window._preflight_export(window.options_from_screen())],
                                 ["Book - JPEG pages", "Book - Cover.jpg"])
            finally:
                window.close()

    def test_lossless_png_checkbox_is_extra_and_grayed_when_pages_are_png_already(self):
        from .gui import ARCHIVE_CHOICES
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "pages"
            folder.mkdir()
            Image.new("RGB", (60, 80), "white").save(folder / "000.png")
            window = ComicWindow()
            try:
                window.scan_done(scan_manual_folder(folder)); window.show_export()
                window.book_name.setText("Book")
                self.assertEqual(window.keep_images.text(), "Also keep lossless PNG pages")
                window.keep_images.setChecked(False)
                window.opt.format = "jpeg"
                window.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index("none"))
                # No archive no longer forces PNG pages: JPEG pages, PNG optional.
                self.assertFalse(window.keep_images.isChecked())
                self.assertTrue(window.keep_images.isEnabled())
                self.assertTrue(window.create_button.isEnabled())
                self.assertEqual([p.name for p in window._preflight_export(window.options_from_screen())],
                                 ["Book - JPEG pages"])
                window.opt.format = "png"
                window.refresh_export()
                self.assertFalse(window.keep_images.isEnabled())
                window.opt.size_output = "both"
                window.opt.reduced_custom, window.opt.reduced_settings = True, {"format": "webp"}
                window.refresh_export()
                self.assertTrue(window.keep_images.isEnabled())  # The reduced copy is WebP.
                window.archive_format.setCurrentIndex(ARCHIVE_CHOICES.index("cbz"))
                window.opt.size_output, window.opt.reduced_custom = "full", False
                window.refresh_export()
                self.assertTrue(window.keep_images.isEnabled())
            finally:
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
                self.assertIn('This export will use “Book - New export 2”', window.export_name_notice)
                export_book(window.plan, opt)
                self.assertTrue(all(path.exists() for path in outputs))
                for name, data in originals.items():
                    self.assertEqual((folder.parent / name).read_bytes(), data)
                window._preflight_export(opt)
                self.assertNotEqual(opt.book_name, "Book - New export 2")
            finally:
                window.close()

    def test_final_review_swap_drag_join_and_component_rotations(self):
        from .ui.thumbnails import PageThumbnail
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

    def test_dragging_a_page_to_an_end_of_the_strip_keeps_scrolling(self):
        # Eric moved a back cover from page 2 to the end of a 140-page book by
        # dragging, scrolling and dragging again; holding at the end now scrolls.
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for i in range(40):
                Image.new('RGB', (60, 80), (30 + i * 5, 20, 10)).save(folder / f'{i:03d}.png')
            window = ComicWindow()
            try:
                window.resize(900, 700)
                window.show(); window.scan_done(scan_manual_folder(folder)); window.enter_preview()
                self.wait_for(lambda: len(getattr(window, 'thumb_buttons', [])) == 40)
                self.app.processEvents(); window._resize_thumbnail_strip(); self.app.processEvents()
                bar = window.thumb_area.horizontalScrollBar()
                bar.setValue(0)
                self.assertGreater(bar.maximum(), 0)
                source = window.thumb_buttons[1]
                width = window.thumb_area.viewport().width()
                # Away from the ends: no scrolling.
                self.assertTrue(window.update_page_drop(source, width // 2))
                self.assertFalse(window.drag_scroll_timer.isActive())
                # At the right end: the strip scrolls on its own, and the drop point follows.
                window.update_page_drop(source, width - 5)
                self.assertTrue(window.drag_scroll_timer.isActive())
                for _ in range(400):
                    window._drag_scroll_tick()
                    if not window.drag_scroll_timer.isActive():
                        break
                self.assertEqual(bar.value(), bar.maximum())
                self.assertFalse(window.drag_scroll_timer.isActive())  # Stops at the end.
                self.assertEqual(window.page_drop_boundary, 40)
                window.finish_page_drop(source, width - 5 + bar.value())
                self.wait_for(lambda: window.plan.entries()[-1] == (1, None))
                # And back the other way, from the left end.
                source = window.thumb_buttons[-1]
                bar.setValue(bar.maximum())
                window.update_page_drop(source, bar.value() + 3)
                for _ in range(400):
                    window._drag_scroll_tick()
                    if not window.drag_scroll_timer.isActive():
                        break
                self.assertEqual((bar.value(), window.page_drop_boundary), (0, 0))
                window.clear_page_drop()
                self.assertIsNone(window.drag_scroll)
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
                with patch("comic_stitcher.gui.QFileDialog.getOpenFileNames",
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
            with patch("comic_stitcher.gui.diagnostics.log_path", return_value=root / "app" / "logs" / "run.log"):
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
                         patch("comic_stitcher.ui.import_flow.scan_folder", side_effect=AssertionError("Rescanned")):
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
            dialog = PdfImportDialog(source, 200)
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
                self.assertEqual(window.size(), START_SIZE.expandedTo(window.start_page.minimumSizeHint()))
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
                window.settings.setValue("workers", 2)
                self.assertEqual(window.options_from_screen().workers, 2)
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
                names = [path.name for path in queued_outputs(window.plan, window.options_from_screen())]
                self.assertTrue(any(name.endswith("Full.cbz") for name in names))
                self.assertTrue(any(name.endswith("Reduced.cbz") for name in names))
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
                window.settings.setValue("workers", 0)
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
