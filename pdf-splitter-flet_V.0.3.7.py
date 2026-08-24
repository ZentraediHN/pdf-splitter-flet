#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MIT License

Copyright (c) 2026 Jorge Laínez <jorge.lainezhn@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import os
import math
import shutil
import zipfile
import threading
import queue
import asyncio
import time

import flet as ft
import pymupdf as fitz

from pathlib import Path

# Limpiar caché conflictiva de Flet para evitar WinError 183 en Windows
try:
  client_path = Path.home() / ".flet" / "client"
  if client_path.exists():
    for p in client_path.glob("flet-desktop-full-*"):
      if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
except Exception:
  pass

class Point:
    """Clase auxiliar para el motor adaptativo de búsqueda"""
    def __init__(self, pages, mb):
        self.pages = pages
        self.mb = mb


class PDFSplitterFletApp:

    def __init__(self, page: ft.Page):
        self.page = page

        # ==================================================
        # CONFIGURACIÓN DE VENTANA
        # ==================================================
        self.page.title = "Divisor de PDFs - Flet (Multimodo RAM)"
        self.page.window.width = 780
        self.page.window.height = 850
        self.page.window.min_width = 650
        self.page.window.min_height = 700
        self.page.padding = 15

        # ==================================================
        # ESTADO
        # ==================================================
        self.pdf_path = ""
        self.output_dir = ""
        self.is_processing = False
        self.processing_start_time = None
        self.message_queue = queue.Queue()

        # ==================================================
        # CACHÉ Y VARIABLES DEL BUSCADOR
        # ==================================================
        self.tested_measurements = set()
        self.tested_cache = {}
        self.recent_density = 0.0

        # ==================================================
        # DIAGNÓSTICO
        # ==================================================
        self.diagnostic = {
            "initial_read": 0.0,
            "binary_search": 0.0,
            "fixed_pages_split": 0.0,
            "final_writing": 0.0,
            "split_total": 0.0,
            "zip": 0.0,
            "writer_append": 0.0,
            "ram_write": 0.0,
            "size_check": 0.0,
            "tests": 0,
        }

        # ==================================================
        # CONTROLES DE UI
        # ==================================================
        self.pdf_field = ft.TextField(
            label="Archivo PDF de entrada",
            read_only=True,
            expand=True,
        )

        self.output_field = ft.TextField(
            label="Carpeta de salida",
            read_only=True,
            expand=True,
        )

        # Radio de Criterio de División
        self.mode_radio = ft.RadioGroup(
            value="size",
            on_change=self.on_mode_changed,
            content=ft.Row(
                controls=[
                    ft.Radio(value="size", label="Dividir por tamaño máximo (MB)"),
                    ft.Radio(value="pages", label="Dividir por cantidad de páginas por parte"),
                ],
                spacing=20,
            )
        )

        # Campo Tamaño MB con botones de incremento/decremento
        self.max_size_field = ft.TextField(
            label="Límite (MB)",
            value="10.0",
            width=90,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            content_padding=5,
        )
        self.btn_minus_size = ft.IconButton(
            icon=ft.Icons.REMOVE,
            on_click=self.decrement_size,
            tooltip="Disminuir 1 MB"
        )
        self.btn_plus_size = ft.IconButton(
            icon=ft.Icons.ADD,
            on_click=self.increment_size,
            tooltip="Aumentar 1 MB"
        )

        # Campo Páginas por parte con botones de incremento/decremento
        self.pages_field = ft.TextField(
            label="Páginas por parte",
            value="50",
            width=90,
            keyboard_type=ft.KeyboardType.NUMBER,
            text_align=ft.TextAlign.CENTER,
            disabled=True,
            content_padding=5,
        )
        self.btn_minus_pages = ft.IconButton(
            icon=ft.Icons.REMOVE,
            on_click=self.decrement_pages,
            disabled=True,
            tooltip="Disminuir 10 páginas"
        )
        self.btn_plus_pages = ft.IconButton(
            icon=ft.Icons.ADD,
            on_click=self.increment_pages,
            disabled=True,
            tooltip="Aumentar 10 páginas"
        )

        # Opción de guardado configurada en una fila horizontal (Row)
        self.export_mode = ft.RadioGroup(
            value="both",
            content=ft.Row(
                controls=[
                    ft.Radio(value="pdf", label="Solo partes PDF"),
                    ft.Radio(value="zip", label="Solo archivo ZIP"),
                    ft.Radio(value="both", label="PDF y ZIP"),
                ],
                spacing=25,
            ),
        )

        self.process_button = ft.Button(
            content=ft.Row([
                ft.Icon(ft.Icons.PICTURE_AS_PDF),
                ft.Text("Procesar y dividir PDF")
            ], tight=True, spacing=8),
            on_click=self.start_process,
        )

        self.progress = ft.ProgressRing(visible=False, width=24, height=24)

        self.log_field = ft.TextField(
            label="Registro de operaciones",
            multiline=True,
            read_only=True,
            expand=True,
        )

        self.create_ui()
        self.page.run_task(self.process_message_queue)

    # ======================================================
    # MÉTODOS PARA BANDERAS Y NÚMEROS (STEPPERS)
    # ======================================================
    def increment_size(self, e):
        try:
            val = float(self.max_size_field.value.replace(",", "."))
            self.max_size_field.value = f"{max(0.5, val + 1.0):.1f}"
            self.page.update()
        except ValueError:
            self.max_size_field.value = "10.0"
            self.page.update()

    def decrement_size(self, e):
        try:
            val = float(self.max_size_field.value.replace(",", "."))
            self.max_size_field.value = f"{max(0.5, val - 1.0):.1f}"
            self.page.update()
        except ValueError:
            self.max_size_field.value = "10.0"
            self.page.update()

    def increment_pages(self, e):
        try:
            val = int(self.pages_field.value)
            self.pages_field.value = str(max(1, val + 10))
            self.page.update()
        except ValueError:
            self.pages_field.value = "50"
            self.page.update()

    def decrement_pages(self, e):
        try:
            val = int(self.pages_field.value)
            self.pages_field.value = str(max(1, val - 10))
            self.page.update()
        except ValueError:
            self.pages_field.value = "50"
            self.page.update()

    # ======================================================
    # CONSTRUCCIÓN DE LA INTERFAZ
    # ======================================================
    def create_ui(self):
        pdf_group = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Archivo PDF de Entrada", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
                    ft.Row(
                        controls=[
                            self.pdf_field,
                            ft.Button(
                                content=ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN), ft.Text("Buscar PDF...")], tight=True, spacing=8),
                                on_click=self.select_pdf
                            ),
                        ],
                    ),
                ],
                spacing=5,
            ),
            padding=5,
        )

        output_group = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Carpeta de Salida", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
                    ft.Row(
                        controls=[
                            self.output_field,
                            ft.Button(
                                content=ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN), ft.Text("Buscar carpeta...")], tight=True, spacing=8),
                                on_click=self.select_output
                            ),
                        ],
                    ),
                ],
                spacing=5,
            ),
            padding=5,
        )

        config_group = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Criterio de División", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
                    self.mode_radio,
                    ft.Row(
                        controls=[
                            ft.Row([self.btn_minus_size, self.max_size_field, self.btn_plus_size], spacing=0),
                            ft.VerticalDivider(width=20),
                            ft.Row([self.btn_minus_pages, self.pages_field, self.btn_plus_pages], spacing=0),
                        ],
                        alignment=ft.MainAxisAlignment.START,
                        spacing=10,
                    )
                ],
                spacing=5,
            ),
            padding=5,
        )

        save_group = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("Opción de Guardado", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
                    self.export_mode,
                ],
                spacing=5,
            ),
            padding=5,
        )

        process_row = ft.Row(
            controls=[self.process_button, self.progress],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=15,
        )

        # El log se envuelve en un contenedor expansible
        log_group = ft.Container(
            content=self.log_field,
            expand=True,
            padding=5,
        )

        main_column = ft.Column(
            controls=[
                pdf_group,
                output_group,
                config_group,
                save_group,
                process_row,
                ft.Divider(height=10),
                log_group,
            ],
            expand=True,
            spacing=10,
        )

        self.page.add(main_column)

    def on_mode_changed(self, e):
        is_size = self.mode_radio.value == "size"
        self.max_size_field.disabled = not is_size
        self.btn_minus_size.disabled = not is_size
        self.btn_plus_size.disabled = not is_size

        self.pages_field.disabled = is_size
        self.btn_minus_pages.disabled = is_size
        self.btn_plus_pages.disabled = is_size

        self.page.update()

    # ======================================================
    # MANEJOS DE ARCHIVOS PICKER
    # ======================================================
    async def select_pdf(self, e):
        try:
            files = await ft.FilePicker().pick_files(
                dialog_title="Seleccionar archivo PDF",
                allow_multiple=False,
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["pdf"],
            )
            if not files or not files[0].path:
                return
            self.pdf_path = files[0].path
            self.pdf_field.value = self.pdf_path
            if not self.output_dir:
                self.output_dir = os.path.dirname(self.pdf_path)
                self.output_field.value = self.output_dir
            self.page.update()
        except Exception as err:
            self.show_error(f"Error al seleccionar PDF:\n\n{err}")

    async def select_output(self, e):
        try:
            directory = await ft.FilePicker().get_directory_path(dialog_title="Seleccionar carpeta de salida")
            if not directory:
                return
            self.output_dir = directory
            self.output_field.value = directory
            self.page.update()
        except Exception as err:
            self.show_error(f"Error al seleccionar carpeta:\n\n{err}")

    # ======================================================
    # LOG Y COLA DE MENSAJES ASÍNCRONA
    # ======================================================
    def log(self, text):
        self.message_queue.put(("log", text))

    async def process_message_queue(self):
        while True:
            try:
                while True:
                    msg_type, data = self.message_queue.get_nowait()
                    if msg_type == "log":
                        self.log_field.value += data + "\n"
                    elif msg_type == "success":
                        self.show_success(data)
                    elif msg_type == "error":
                        self.show_error(data)
                    elif msg_type == "finished":
                        self.process_button.disabled = False
                        self.progress.visible = False
                    self.page.update()
            except queue.Empty:
                pass
            await asyncio.sleep(0.05)

    def clear_log(self):
        self.log_field.value = ""
        self.page.update()

    # ======================================================
    # LÓGICA DE PROCESAMIENTO (PyMuPDF / RAM)
    # ======================================================
    def get_file_size_mb(self, path):
        return os.path.getsize(path) / (1024 * 1024)

    def real_measure(self, pdf, start, end, limit_mb, test_number):
        key = (start, end)
        if key in self.tested_measurements:
            return None

        self.tested_measurements.add(key)
        t0 = time.perf_counter()

        if key in self.tested_cache:
            mb = self.tested_cache[key]
        else:
            append_start = time.perf_counter()
            new_doc = fitz.open()
            new_doc.insert_pdf(pdf, from_page=start - 1, to_page=end - 1)
            self.diagnostic["writer_append"] += time.perf_counter() - append_start

            write_start = time.perf_counter()
            pdf_bytes = new_doc.tobytes()
            size_mb = len(pdf_bytes) / (1024 * 1024)
            new_doc.close()
            self.diagnostic["ram_write"] += time.perf_counter() - write_start

            self.tested_cache[key] = size_mb
            self.diagnostic["tests"] += 1
            mb = size_mb

        elapsed = time.perf_counter() - t0
        pages = end - start + 1
        status = "OK" if mb <= limit_mb else "EXCEDE"

        self.log(f"      🔬 REAL RAM {test_number:2d}: págs {start}-{end} ({pages} págs) → {mb:9.4f} MB {status} | {elapsed:5.2f} s")
        return Point(pages, mb)

    def find_cut(self, pdf, start, total_pages, limit_mb, global_density):
        available = total_pages - start + 1
        
        internal_target = limit_mb * 0.95
        limit_safety_mb = internal_target - 0.0005
        acceptance_mb = internal_target * 0.97

        if available <= 1:
            pt = self.real_measure(pdf, start, total_pages, limit_safety_mb, 1)
            return total_pages, pt.mb, 1

        low, high, best_ok = None, None, None
        real_count = 0

        density = (global_density * 0.2) + (self.recent_density * 0.8)
        target_pages = int(limit_safety_mb / density)
        candidate_pages = max(10, min(available, target_pages))

        while real_count < 10:
            candidate_pages = max(1, min(candidate_pages, available))

            while (start, start + candidate_pages - 1) in self.tested_measurements:
                if low and high:
                    candidate_pages = (low.pages + high.pages) // 2
                    if (start, start + candidate_pages - 1) in self.tested_measurements:
                        break
                elif low:
                    candidate_pages += 1
                elif high:
                    candidate_pages -= 1
                else:
                    break

            end = start + candidate_pages - 1
            if end > total_pages or candidate_pages < 1 or (start, end) in self.tested_measurements:
                break

            pt = self.real_measure(pdf, start, end, limit_safety_mb, real_count + 1)
            if not pt:
                break
            real_count += 1

            if pt.mb <= limit_safety_mb:
                if best_ok is None or pt.pages > best_ok.pages:
                    best_ok = pt
                if low is None or pt.pages > low.pages:
                    low = pt
                self.recent_density = pt.mb / pt.pages
            else:
                if high is None or pt.pages < high.pages:
                    high = pt

            if pt.mb >= acceptance_mb and pt.mb <= limit_safety_mb:
                return end, pt.mb, real_count
                
            if pt.pages == available and pt.mb <= limit_safety_mb:
                return end, pt.mb, real_count

            if low and high:
                gap = high.pages - low.pages
                if gap <= 1:
                    return start + low.pages - 1, low.mb, real_count
                if gap <= 10:
                    candidate_pages = (low.pages + high.pages) // 2
                else:
                    if high.mb != low.mb:
                        pred = low.pages + (limit_safety_mb - low.mb) * (high.pages - low.pages) / (high.mb - low.mb)
                        candidate_pages = int(round(pred))
                    else:
                        candidate_pages = (low.pages + high.pages) // 2
                    
                    if candidate_pages <= low.pages + 1 or candidate_pages >= high.pages - 1:
                        candidate_pages = (low.pages + high.pages) // 2
            elif low:
                rem_mb = limit_safety_mb - low.mb
                add_pages = max(1, int(rem_mb / (low.mb / low.pages)))
                candidate_pages = low.pages + add_pages
            elif high:
                ratio = (limit_safety_mb / high.mb) * 0.95 
                candidate_pages = max(1, int(high.pages * ratio))

        if best_ok:
            return start + best_ok.pages - 1, best_ok.mb, real_count

        return start, 0.0, real_count

    def split_pdf_by_max_size(self, pdf_path, output_dir, max_size_mb):
        split_start = time.perf_counter()

        reader_start = time.perf_counter()
        pdf = fitz.open(pdf_path)
        total_pages = len(pdf)
        self.diagnostic["initial_read"] = time.perf_counter() - reader_start

        source_size_mb = self.get_file_size_mb(pdf_path)
        global_density = source_size_mb / total_pages
        
        self.recent_density = global_density
        self.tested_measurements = set()
        self.tested_cache = {}

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        output_files = []
        current_page = 1
        actual_parts = 0

        try:
            while current_page <= total_pages:
                actual_parts += 1
                self.log(f"\n📦 Buscando parte {actual_parts} desde página {current_page}")

                search_start = time.perf_counter()
                cut_page, part_size, attempts = self.find_cut(
                    pdf, current_page, total_pages, max_size_mb, global_density
                )
                self.diagnostic["binary_search"] += time.perf_counter() - search_start

                final_start = time.perf_counter()
                final_doc = fitz.open()
                final_doc.insert_pdf(pdf, from_page=current_page - 1, to_page=cut_page - 1)

                output_filename = f"{base_name}_parte{actual_parts}.pdf"
                output_path = os.path.join(output_dir, output_filename)
                final_doc.save(output_path)
                final_doc.close()
                
                self.diagnostic["final_writing"] += time.perf_counter() - final_start

                final_size = self.get_file_size_mb(output_path)
                status = "✅" if final_size <= max_size_mb else "❌"
                page_count = cut_page - current_page + 1
                
                self.log(f"   {status} Parte {actual_parts}: págs {current_page}-{cut_page} ({page_count} págs) → {final_size:.4f} MB | {attempts} mediciones RAM")

                output_files.append(output_path)
                current_page = cut_page + 1
        finally:
            pdf.close()

        self.diagnostic["split_total"] = time.perf_counter() - split_start
        return output_files, actual_parts

    def split_pdf_by_fixed_pages(self, pdf_path, output_dir, pages_per_part):
        split_start = time.perf_counter()
        
        reader_start = time.perf_counter()
        pdf = fitz.open(pdf_path)
        total_pages = len(pdf)
        self.diagnostic["initial_read"] = time.perf_counter() - reader_start

        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        output_files = []
        current_page = 1
        actual_parts = 0

        try:
            fixed_start = time.perf_counter()
            while current_page <= total_pages:
                actual_parts += 1
                end_page = min(current_page + pages_per_part - 1, total_pages)
                self.log(f"\n📦 Creando parte {actual_parts}: páginas {current_page} a {end_page}")

                final_doc = fitz.open()
                final_doc.insert_pdf(pdf, from_page=current_page - 1, to_page=end_page - 1)

                output_filename = f"{base_name}_parte{actual_parts}.pdf"
                output_path = os.path.join(output_dir, output_filename)
                
                final_write_start = time.perf_counter()
                final_doc.save(output_path)
                final_doc.close()
                self.diagnostic["final_writing"] += time.perf_counter() - final_write_start

                final_size = self.get_file_size_mb(output_path)
                page_count = end_page - current_page + 1
                self.log(f"   ✅ Parte {actual_parts}: págs {current_page}-{end_page} ({page_count} págs) → {final_size:.4f} MB")

                output_files.append(output_path)
                current_page = end_page + 1

            self.diagnostic["fixed_pages_split"] = time.perf_counter() - fixed_start
        finally:
            pdf.close()

        self.diagnostic["split_total"] = time.perf_counter() - split_start
        return output_files, actual_parts

    # ======================================================
    # CONTROL DE HILO Y EJECUCIÓN
    # ======================================================
    def start_process(self, e):
        if self.is_processing:
            return

        pdf_path = self.pdf_path.strip()
        output_dir = self.output_dir.strip()

        if not os.path.isfile(pdf_path) or not os.path.isdir(output_dir):
            self.show_error("Las rutas proporcionadas no son válidas.")
            return

        mode_type = self.mode_radio.value
        try:
            if mode_type == "size":
                param_value = float(self.max_size_field.value.replace(",", "."))
                if param_value < 0.5 or param_value > 500:
                    self.show_error("El tamaño debe estar entre 0.5 y 500 MB.")
                    return
            else:
                param_value = int(self.pages_field.value)
                if param_value < 1 or param_value > 10000:
                    self.show_error("Las páginas por parte deben estar entre 1 y 10000.")
                    return
        except ValueError:
            self.show_error("Por favor ingresa un valor numérico válido.")
            return

        export_mode = self.export_mode.value

        self.diagnostic = {k: 0.0 if k != "tests" else 0 for k in self.diagnostic}
        self.clear_log()
        self.is_processing = True
        self.processing_start_time = time.perf_counter()
        self.process_button.disabled = True
        self.progress.visible = True
        self.page.update()

        threading.Thread(
            target=self.run_pdf_process,
            args=(pdf_path, output_dir, mode_type, param_value, export_mode),
            daemon=True
        ).start()

    def run_pdf_process(self, pdf_path, output_dir, mode_type, param_value, export_mode):
        try:
            self.log("🚀 Iniciando procesamiento FLET con motor PyMuPDF (RAM)...")
            original_size = self.get_file_size_mb(pdf_path)
            self.log(f"📄 Archivo: {os.path.basename(pdf_path)} ({original_size:.2f} MB)")

            base_name = os.path.splitext(os.path.basename(pdf_path))[0]

            if mode_type == "size":
                max_size_mb = param_value
                if original_size <= max_size_mb:
                    self.log(f"\n✅ {original_size:.2f} MB ≤ {max_size_mb:.2f} MB → No requiere división.")
                    final_path = os.path.join(output_dir, f"{base_name}_copia.pdf")
                    shutil.copy2(pdf_path, final_path)
                    parts = [final_path]
                else:
                    self.log(f"\n⚠️ {original_size:.2f} MB > {max_size_mb:.2f} MB → Iniciando división inteligente en RAM...")
                    parts, actual_parts = self.split_pdf_by_max_size(pdf_path, output_dir, max_size_mb)
            else:
                pages_per_part = int(param_value)
                self.log(f"\n⚡ Iniciando división fija por bloques de {pages_per_part} páginas...")
                parts, actual_parts = self.split_pdf_by_fixed_pages(pdf_path, output_dir, pages_per_part)

            # MANEJO DE ZIP
            if export_mode in ("zip", "both"):
                zip_start = time.perf_counter()
                zip_filename = os.path.join(output_dir, f"{base_name}_procesado.zip")
                with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
                    for part in parts:
                        zipf.write(part, os.path.basename(part))
                self.diagnostic["zip"] += time.perf_counter() - zip_start
                self.log(f"\n📦 Archivo ZIP creado: {zip_filename}")

            if export_mode == "zip":
                for part in parts:
                    if os.path.exists(part): 
                        os.remove(part)
                self.log("🗑️ Archivos PDF individuales eliminados.")
            elif export_mode == "pdf":
                self.log("\n📄 Se conservan únicamente las partes PDF.")

            elapsed_text = self.format_elapsed_time(time.perf_counter() - self.processing_start_time)
            self.log(f"\n✅ ¡Proceso completado exitosamente en {elapsed_text}!")
            self.print_diagnostic(mode_type)
            self.message_queue.put(("success", elapsed_text))

        except Exception as e:
            self.log(f"\n❌ Error: {str(e)}")
            self.message_queue.put(("error", str(e)))
        finally:
            self.is_processing = False
            self.message_queue.put(("finished", None))

    # ======================================================
    # UTILIDADES Y DIAGNÓSTICO
    # ======================================================
    def format_elapsed_time(self, seconds):
        if seconds < 60: 
            return f"{seconds:.2f} segundos"
        minutes = int(seconds // 60)
        rem_sec = seconds - minutes * 60
        if minutes < 60: 
            return f"{minutes} min {rem_sec:.2f} s"
        return f"{int(minutes // 60)} h, {minutes % 60} min y {rem_sec:.2f} s"

    def print_diagnostic(self, mode_type):
        d = self.diagnostic
        t_total = time.perf_counter() - self.processing_start_time
        
        self.log("\n══════════════════════════════════════")
        self.log("📊 DIAGNÓSTICO Flet + PyMuPDF (RAM)")
        self.log("══════════════════════════════════════")
        self.log(f"📖 Lectura inicial PDF: {d['initial_read']:.2f} s")
        if mode_type == "size":
            self.log(f"🔍 Búsqueda adaptativa en RAM: {d['binary_search']:.2f} s")
            self.log(f"   ├─ Pruebas virtuales: {d['tests']}")
            self.log(f"   ├─ Inserción de páginas: {d['writer_append']:.2f} s")
            self.log(f"   └─ Volcado a RAM (tobytes): {d['ram_write']:.2f} s")
        else:
            self.log(f"⚡ División por páginas fijas: {d['fixed_pages_split']:.2f} s")
        self.log(f"💾 Escritura partes finales: {d['final_writing']:.2f} s")
        self.log(f"✂️ División completa: {d['split_total']:.2f} s")
        self.log(f"📦 Creación ZIP: {d['zip']:.2f} s")
        self.log("──────────────────────────────────────")
        self.log(f"⏱️ TIEMPO TOTAL: {self.format_elapsed_time(t_total)}")
        self.log("══════════════════════════════════════")

    def show_error(self, message):
        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.Icons.ERROR, color=ft.Colors.RED), ft.Text("Error")]),
            content=ft.Text(message),
            actions=[ft.Button(content=ft.Text("Aceptar"), on_click=lambda e: self.close_dialog(dialog))]
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def show_success(self, elapsed_text):
        dialog = ft.AlertDialog(
            title=ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN), ft.Text("Éxito")]),
            content=ft.Text(f"El PDF se procesó correctamente.\n\nTiempo total: {elapsed_text}"),
            actions=[ft.Button(content=ft.Text("Aceptar"), on_click=lambda e: self.close_dialog(dialog))]
        )
        self.page.dialog = dialog
        dialog.open = True
        self.page.update()

    def close_dialog(self, dialog):
        dialog.open = False
        self.page.update()


def main(page: ft.Page):
    PDFSplitterFletApp(page)

if __name__ == "__main__":
    ft.run(main)
