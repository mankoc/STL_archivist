//! The web client's model viewer: MeshPeek's renderer (`meshpeek-render`) drawing the scenes the server sent
//! (`meshpeek_core::wire`, one per model file) into a canvas, through WebGPU or WebGL2.
//!
//! Several files can be shown at once, each keyed by its name in the archive, with its own colour, material,
//! wireframe and visibility. The page owns the input: it turns pointer, wheel and key events into the camera
//! moves below (`web/src/viewer/input.ts`, MeshPeek's mouse mapping) and asks for a frame after each. Pixel
//! arguments are canvas pixels (CSS pixels times the device pixel ratio).
//!
//! Built with `npm run gen:viewer` (web/), which writes `web/src/viewer/wasm/`.

#![cfg(target_arch = "wasm32")]

use std::sync::Arc;

use glam::{DMat4, DVec2, DVec3, DVec4};
use meshpeek_core::{Mesh, ObjectKind, Plate, wire};
use meshpeek_render::{Camera, GpuMesh, Material, ObjectId, Palette, Renderer, Style, wgpu};
use wasm_bindgen::prelude::*;

/// MeshPeek's material presets.
const MATERIALS: [(&str, Material); 4] = [
    ("Matte", Material { ambient: 0.15, diffuse: 0.9, specular: 0.0, specular_power: 1.0 }),
    ("Plastic", Material { ambient: 0.1, diffuse: 0.8, specular: 0.3, specular_power: 20.0 }),
    ("Glossy", Material { ambient: 0.1, diffuse: 0.7, specular: 1.0, specular_power: 10.0 }),
    ("Metal", Material { ambient: 0.3, diffuse: 0.45, specular: 1.0, specular_power: 12.0 }),
];
/// MeshPeek's default model and support colours, and its "Colour each model" colours.
const MODEL_COLOR: [u8; 3] = [0xc3, 0xcc, 0xd9];
const SUPPORT_COLOR: [u8; 3] = [0xe8, 0xa2, 0x4a];
const MODEL_COLORS: [[u8; 3]; 8] = [
    [0x4e, 0x9a, 0xe6],
    [0xe6, 0x6a, 0x4e],
    [0x5c, 0xc2, 0x7a],
    [0xe6, 0xc2, 0x4e],
    [0xb0, 0x6a, 0xe6],
    [0x4e, 0xd6, 0xd6],
    [0xe6, 0x6a, 0xb0],
    [0xa0, 0xa8, 0xb4],
];

/// `#rrggbb`.
fn parse_hex(color: &str) -> Option<[u8; 3]> {
    let hex = color.strip_prefix('#').filter(|h| h.len() == 6)?;
    let byte = |i: usize| u8::from_str_radix(&hex[i..i + 2], 16).ok();
    Some([byte(0)?, byte(2)?, byte(4)?])
}

/// Direction from the focal point to the camera, and view-up, of MeshPeek's standard views.
fn standard_view(name: &str) -> Option<(DVec3, DVec3)> {
    Some(match name {
        "isometric" => (DVec3::new(1.0, 1.0, 1.0), DVec3::Z),
        "front" => (-DVec3::Y, DVec3::Z),
        "back" => (DVec3::Y, DVec3::Z),
        "left" => (-DVec3::X, DVec3::Z),
        "right" => (DVec3::X, DVec3::Z),
        "top" => (DVec3::Z, DVec3::Y),
        "bottom" => (-DVec3::Z, DVec3::Y),
        _ => return None,
    })
}

fn js_error(e: impl std::fmt::Display) -> JsError {
    JsError::new(&e.to_string())
}

/// What [`Viewer::add`] put in the view.
#[wasm_bindgen]
pub struct Loaded {
    /// Objects (models and supports).
    #[wasm_bindgen(readonly)]
    pub objects: u32,
    /// Triangles in all objects.
    #[wasm_bindgen(readonly)]
    pub triangles: f64,
    skipped: Vec<String>,
}

#[wasm_bindgen]
impl Loaded {
    /// `"<name>: <reason>"` for the objects the file has but could not be loaded.
    #[wasm_bindgen(getter)]
    pub fn skipped(&self) -> Vec<String> {
        self.skipped.clone()
    }
}

/// Facts about a shown file, for its information panel (MeshPeek's "Information").
#[wasm_bindgen]
pub struct FileInfo {
    /// Objects (models and supports).
    #[wasm_bindgen(readonly)]
    pub objects: u32,
    /// Objects that are models.
    #[wasm_bindgen(readonly)]
    pub models: u32,
    /// Triangles of the models.
    #[wasm_bindgen(readonly)]
    pub triangles: f64,
    /// Triangles of the supports (and rafts).
    #[wasm_bindgen(readonly)]
    pub support_triangles: f64,
    size: Vec<f64>,
    facts: Vec<String>,
}

#[wasm_bindgen]
impl FileInfo {
    /// Width, depth and height (X, Y, Z) of everything in the file, in its units (mm); empty without geometry.
    #[wasm_bindgen(getter)]
    pub fn size(&self) -> Vec<f64> {
        self.size.clone()
    }

    /// What the file says about itself (format, file size, slicer, printer…) as label, value, label, value…
    #[wasm_bindgen(getter)]
    pub fn facts(&self) -> Vec<String> {
        self.facts.clone()
    }
}

/// One object of a shown file: the CPU mesh is kept to add edge buffers when its wireframe is first asked for.
struct Part {
    id: ObjectId,
    /// Object to world, as the renderer draws it.
    transform: DMat4,
    kind: ObjectKind,
    mesh: Mesh,
    has_edges: bool,
    info: Vec<(String, String)>,
}

/// A shown file and how it is drawn.
struct File {
    key: String,
    parts: Vec<Part>,
    plates: Vec<Plate>,
    color: Option<[u8; 3]>,
    material: Material,
    wire: bool,
    visible: bool,
    /// How far the file is moved from where it places itself (spreading files out).
    offset: DVec3,
}

/// A canvas showing one or more scenes.
#[wasm_bindgen]
pub struct Viewer {
    device: wgpu::Device,
    queue: wgpu::Queue,
    surface: wgpu::Surface<'static>,
    config: wgpu::SurfaceConfiguration,
    renderer: Renderer,
    camera: Camera,
    blit: Blit,
    backend: String,
    files: Vec<File>,
    color_each: bool,
}

#[wasm_bindgen]
impl Viewer {
    /// Draw into `canvas` (sized by the page) with WebGPU when the browser has it, otherwise WebGL2;
    /// `webgl` forces WebGL2. A canvas WebGPU failed on cannot be used for WebGL2: retry on a new one.
    pub async fn create(canvas: web_sys::HtmlCanvasElement, webgl: bool) -> Result<Viewer, JsError> {
        console_error_panic_hook::set_once();
        let (width, height) = (canvas.width().max(1), canvas.height().max(1));
        let backends = if webgl { wgpu::Backends::GL } else { wgpu::Backends::BROWSER_WEBGPU | wgpu::Backends::GL };
        let instance =
            wgpu::util::new_instance_with_webgpu_detection(&wgpu::InstanceDescriptor { backends, ..Default::default() })
                .await;
        let surface = instance.create_surface(wgpu::SurfaceTarget::Canvas(canvas)).map_err(js_error)?;
        let adapter = instance
            .request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::HighPerformance,
                compatible_surface: Some(&surface),
                force_fallback_adapter: false,
            })
            .await
            .map_err(js_error)?;
        let (device, queue) = adapter
            .request_device(&wgpu::DeviceDescriptor {
                label: Some("stl-viewer"),
                required_features: wgpu::Features::empty(),
                required_limits: adapter.limits(), // large buffers for big models
                ..Default::default()
            })
            .await
            .map_err(js_error)?;
        let caps = surface.get_capabilities(&adapter);
        // The renderer's image is in gamma space: a plain (not sRGB) canvas shows it as it is.
        let format = caps.formats.iter().copied().find(|f| !f.is_srgb()).or(caps.formats.first().copied()).ok_or_else(
            || JsError::new("the canvas offers no image format"),
        )?;
        let alpha_mode = if caps.alpha_modes.contains(&wgpu::CompositeAlphaMode::Opaque) {
            wgpu::CompositeAlphaMode::Opaque
        } else {
            caps.alpha_modes.first().copied().unwrap_or(wgpu::CompositeAlphaMode::Auto)
        };
        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            format,
            width,
            height,
            present_mode: wgpu::PresentMode::Fifo,
            desired_maximum_frame_latency: 2,
            alpha_mode,
            view_formats: vec![],
        };
        surface.configure(&device, &config);
        let renderer = Renderer::new(&device, Renderer::best_sample_count(&adapter));
        let blit = Blit::new(&device, format);
        let info = adapter.get_info();
        let backend = match info.backend {
            wgpu::Backend::BrowserWebGpu => "WebGPU".to_string(),
            wgpu::Backend::Gl => "WebGL2".to_string(),
            other => format!("{other:?}"),
        };
        Ok(Viewer {
            device,
            queue,
            surface,
            config,
            renderer,
            camera: Camera::default(),
            blit,
            backend,
            files: Vec::new(),
            color_each: false,
        })
    }

    /// "WebGPU" or "WebGL2".
    #[wasm_bindgen(getter)]
    pub fn backend(&self) -> String {
        self.backend.clone()
    }

    /// Show the file `key` (a scene from `GET /api/models/{id}/view`), replacing what it showed before. The
    /// first file shown is seen from the isometric view; later ones are framed with everything, keeping the
    /// viewing direction.
    pub fn add(&mut self, key: &str, data: &[u8]) -> Result<Loaded, JsError> {
        let scene = wire::decode(data).map_err(js_error)?;
        self.remove(key);
        let first = self.files.is_empty();
        let max_buffer = self.device.limits().max_buffer_size;
        let mut parts = Vec::new();
        let mut triangles = 0;
        for object in scene.objects {
            triangles += object.mesh.n_faces();
            // The renderer takes an Arc (MeshPeek shares meshes between threads); the browser has one thread.
            #[allow(clippy::arc_with_non_send_sync)]
            let gpu = Arc::new(GpuMesh::upload(&self.device, &object.mesh, max_buffer));
            let id = self.renderer.add_object(&self.device, gpu, object.transform, Style { color: [1.0; 3], material: MATERIALS[1].1 }, object.visible);
            parts.push(Part { id, transform: object.transform, kind: object.kind, mesh: object.mesh, has_edges: false, info: object.info });
        }
        let objects = parts.len() as u32;
        let file = File { key: key.to_string(), parts, plates: scene.plates, color: None, material: MATERIALS[1].1, wire: false, visible: true, offset: DVec3::ZERO };
        self.files.push(file);
        self.restyle_file(self.files.len() - 1);
        self.update_plates();
        if first {
            self.show_view("isometric");
        } else {
            self.fit();
        }
        Ok(Loaded { objects, triangles: triangles as f64, skipped: scene.skipped })
    }

    /// Stop showing the file `key`.
    pub fn remove(&mut self, key: &str) {
        let Some(at) = self.files.iter().position(|f| f.key == key) else { return };
        for part in self.files.remove(at).parts {
            self.renderer.remove_object(part.id);
        }
        if self.color_each {
            self.restyle(); // the files after it move up in "colour each model"
        }
        self.update_plates();
    }

    /// Draw the file's models in `color` (`#rrggbb`; `None` or anything else: as by default), with one of
    /// MeshPeek's materials (`Matte`, `Plastic`, `Glossy` or `Metal`; others keep the current one), as its
    /// edges (`wire`) or as surfaces. Only this file is restyled.
    pub fn set_style(&mut self, key: &str, color: Option<String>, material: &str, wire: bool) {
        let max_buffer = self.device.limits().max_buffer_size;
        let Some(at) = self.files.iter().position(|f| f.key == key) else { return };
        let file = &mut self.files[at];
        file.color = color.as_deref().and_then(parse_hex);
        if let Some(&(_, m)) = MATERIALS.iter().find(|(n, _)| *n == material) {
            file.material = m;
        }
        if file.wire != wire {
            file.wire = wire;
            for part in &mut file.parts {
                if wire && !part.has_edges {
                    #[allow(clippy::arc_with_non_send_sync)]
                    let gpu = Arc::new(GpuMesh::upload_with(&self.device, &part.mesh, max_buffer, true));
                    self.renderer.set_mesh(part.id, gpu);
                    part.has_edges = true;
                }
                self.renderer.set_object_wireframe(part.id, wire);
            }
        }
        self.restyle_file(at);
    }

    /// Show or hide the file (it stays loaded).
    pub fn set_visible(&mut self, key: &str, on: bool) {
        let Some(file) = self.file(key) else { return };
        file.visible = on;
        let ids: Vec<ObjectId> = file.parts.iter().map(|p| p.id).collect();
        for id in ids {
            self.renderer.set_visible(id, on);
        }
        if self.color_each {
            self.restyle(); // "colour each model" counts the shown models
        }
        self.update_plates();
    }

    /// Give each model its own colour (MeshPeek's "Colour each model"); colours set per file still win.
    pub fn set_color_each(&mut self, on: bool) {
        if self.color_each != on {
            self.color_each = on;
            self.restyle();
        }
    }

    /// Show or hide the build plates and volumes of slicer scenes.
    pub fn set_show_plates(&mut self, show: bool) {
        self.renderer.set_show_plates(show);
    }

    /// Facts about the file `key` (none when it is not loaded).
    pub fn file_info(&self, key: &str) -> Option<FileInfo> {
        let file = self.files.iter().find(|f| f.key == key)?;
        let models: Vec<&Part> = file.parts.iter().filter(|p| p.kind == ObjectKind::Model).collect();
        let triangles = |parts: &mut dyn Iterator<Item = &Part>| parts.map(|p| p.mesh.n_faces()).sum::<usize>() as f64;
        let bounds = file.parts.iter().filter_map(|p| self.renderer.object_bounds(p.id)).reduce(|(a0, a1), (b0, b1)| (a0.min(b0), a1.max(b1)));
        let mut facts: Vec<&(String, String)> = Vec::new();
        for fact in file.parts.iter().flat_map(|p| &p.info) {
            if !facts.contains(&fact) {
                facts.push(fact);
            }
        }
        Some(FileInfo {
            objects: file.parts.len() as u32,
            models: models.len() as u32,
            triangles: triangles(&mut models.iter().copied()),
            support_triangles: triangles(&mut file.parts.iter().filter(|p| p.kind == ObjectKind::Support)),
            size: bounds.map(|(lo, hi)| (hi - lo).to_array().to_vec()).unwrap_or_default(),
            facts: facts.into_iter().flat_map(|(label, value)| [label.clone(), value.clone()]).collect(),
        })
    }

    /// The canvas rectangle the shown files cover as drawn now (`x0, y0, x1, y1`
    /// in pixels from the top left, within the canvas; the build plate not
    /// included), from their vertices; empty when nothing is shown.
    pub fn shown_rect(&self) -> Vec<f64> {
        let (w, h) = (self.config.width as f64, self.config.height as f64);
        let (view, projection) = self.camera.matrices(w / h, self.renderer.scene_bounds());
        let clip = (projection * view).as_dmat4();
        let (mut lo, mut hi) = (DVec2::splat(f64::MAX), DVec2::splat(f64::MIN));
        for (file, part) in self.files.iter().filter(|f| f.visible).flat_map(|f| f.parts.iter().map(move |p| (f, p))) {
            let to_clip = clip * DMat4::from_translation(file.offset) * part.transform;
            for v in &part.mesh.vertices {
                let c = to_clip * DVec4::new(v[0] as f64, v[1] as f64, v[2] as f64, 1.0);
                if c.w > 1e-9 {
                    let p = DVec2::new(c.x / c.w, c.y / c.w);
                    (lo, hi) = (lo.min(p), hi.max(p));
                }
            }
        }
        if lo.x > hi.x {
            return Vec::new();
        }
        // Clip space to pixels: x from -1 (left), y from 1 (top).
        let px = |x: f64| ((x + 1.0) / 2.0 * w).clamp(0.0, w);
        let py = |y: f64| ((1.0 - y) / 2.0 * h).clamp(0.0, h);
        vec![px(lo.x), py(hi.y), px(hi.x), py(lo.y)]
    }

    /// Where the file `key` places itself (without its offset): `x0, y0, z0, x1, y1, z1`; empty
    /// when it is not loaded or has no geometry.
    pub fn file_bounds(&self, key: &str) -> Vec<f64> {
        let Some(file) = self.files.iter().find(|f| f.key == key) else { return Vec::new() };
        let bounds = file.parts.iter().filter_map(|p| self.renderer.object_bounds(p.id)).reduce(|(a0, a1), (b0, b1)| (a0.min(b0), a1.max(b1)));
        bounds.map(|(lo, hi)| [(lo - file.offset).to_array(), (hi - file.offset).to_array()].concat()).unwrap_or_default()
    }

    /// Move the file `key` by `dx`, `dy` from where it places itself.
    pub fn set_offset(&mut self, key: &str, dx: f64, dy: f64) {
        let Some(at) = self.files.iter().position(|f| f.key == key) else { return };
        let target = DVec3::new(dx, dy, 0.0);
        let delta = target - self.files[at].offset;
        if delta == DVec3::ZERO {
            return;
        }
        for part in &self.files[at].parts {
            self.renderer.translate(&self.queue, part.id, delta);
        }
        self.files[at].offset = target;
    }

    /// Frame the file's objects.
    pub fn focus(&mut self, key: &str) {
        let Some(file) = self.files.iter().find(|f| f.key == key) else { return };
        let bounds = file.parts.iter().filter_map(|p| self.renderer.object_bounds(p.id)).reduce(|(a0, a1), (b0, b1)| (a0.min(b0), a1.max(b1)));
        if let Some((low, high)) = bounds {
            self.camera.frame(low, high);
        }
    }

    /// Follow the page's theme (background and plate colours).
    pub fn set_dark(&mut self, dark: bool) {
        self.renderer.set_palette(if dark { Palette::DARK } else { Palette::LIGHT });
    }

    /// The camera's right, up and viewing directions in world coordinates (9 numbers), for the view cube.
    pub fn camera_basis(&self) -> Vec<f64> {
        let forward = self.camera.direction();
        let right = forward.cross(self.camera.up).normalize_or(DVec3::X);
        let up = right.cross(forward);
        [right, up, forward].iter().flat_map(|v| v.to_array()).collect()
    }

    /// The canvas is now `width` x `height` pixels.
    pub fn resize(&mut self, width: u32, height: u32) {
        let (width, height) = (width.max(1), height.max(1));
        if (width, height) != (self.config.width, self.config.height) {
            self.config.width = width;
            self.config.height = height;
            self.surface.configure(&self.device, &self.config);
        }
    }

    /// Turntable orbit: `azimuth` about the world Z axis, `elevation` toward the poles, in degrees.
    pub fn orbit(&mut self, azimuth: f64, elevation: f64) {
        self.camera.orbit_turntable(azimuth, elevation);
    }

    /// Move the view by canvas pixels.
    pub fn pan(&mut self, dx: f64, dy: f64) {
        self.camera.pan(dx, dy, self.config.height as f64);
    }

    /// Move toward (`factor` > 1) or away from the focal point.
    pub fn zoom(&mut self, factor: f64) {
        self.camera.dolly(factor);
    }

    /// Frame everything.
    pub fn fit(&mut self) {
        if let Some((low, high)) = self.renderer.scene_bounds() {
            self.camera.frame(low, high);
        }
    }

    /// Look along a standard view (`isometric`, `front`, `back`, `left`, `right`, `top`, `bottom`) and frame
    /// everything; other names are ignored.
    pub fn show_view(&mut self, name: &str) {
        if let Some((direction, up)) = standard_view(name) {
            let distance = self.camera.distance();
            self.camera.view_from(direction, up, distance);
            self.fit();
        }
    }

    /// Draw a frame.
    pub fn render(&mut self) -> Result<(), JsError> {
        let frame = match self.surface.get_current_texture() {
            Ok(frame) => frame,
            Err(wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated) => {
                self.surface.configure(&self.device, &self.config);
                return Ok(()); // the page asks again on the next change
            }
            Err(e) => return Err(js_error(e)),
        };
        let size = [self.config.width, self.config.height];
        let image = self.renderer.render(&self.device, &self.queue, size, &self.camera);
        let target = frame.texture.create_view(&Default::default());
        self.blit.draw(&self.device, &self.queue, image, &target);
        frame.present();
        Ok(())
    }
}

impl Viewer {
    fn file(&mut self, key: &str) -> Option<&mut File> {
        self.files.iter_mut().find(|f| f.key == key)
    }

    /// Every object's style (see [`Viewer::restyle_file`]).
    fn restyle(&mut self) {
        for at in 0..self.files.len() {
            self.restyle_file(at);
        }
    }

    /// The style of the file's objects: its colour, else (models) the next "colour each model" colour (counting
    /// the shown models before it) or the default; supports keep the support colour unless the file has one.
    fn restyle_file(&mut self, at: usize) {
        let models = |f: &File| f.parts.iter().filter(|p| p.kind == ObjectKind::Model).count();
        let mut ordinal: usize = self.files[..at].iter().filter(|f| f.visible).map(models).sum();
        let file = &self.files[at];
        for part in &file.parts {
            let color = match (file.color, part.kind) {
                (Some(c), _) => c,
                (None, ObjectKind::Support) => SUPPORT_COLOR,
                (None, ObjectKind::Model) if self.color_each && file.visible => MODEL_COLORS[ordinal % MODEL_COLORS.len()],
                (None, ObjectKind::Model) => MODEL_COLOR,
            };
            if part.kind == ObjectKind::Model {
                ordinal += 1;
            }
            let style = Style { color: color.map(|c| c as f32 / 255.0), material: file.material };
            self.renderer.set_style(&self.queue, part.id, style);
        }
    }

    /// The build plates of the shown files.
    fn update_plates(&mut self) {
        let plates: Vec<Plate> = self.files.iter().filter(|f| f.visible).flat_map(|f| f.plates.iter().copied()).collect();
        self.renderer.set_plates(plates);
    }
}

/// Copies the renderer's offscreen image onto the canvas, pixel for pixel.
struct Blit {
    layout: wgpu::BindGroupLayout,
    pipeline: wgpu::RenderPipeline,
}

impl Blit {
    fn new(device: &wgpu::Device, format: wgpu::TextureFormat) -> Self {
        let shader = device.create_shader_module(wgpu::include_wgsl!("blit.wgsl"));
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("blit"),
            entries: &[wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Texture {
                    sample_type: wgpu::TextureSampleType::Float { filterable: false },
                    view_dimension: wgpu::TextureViewDimension::D2,
                    multisampled: false,
                },
                count: None,
            }],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("blit"),
            bind_group_layouts: &[&layout],
            push_constant_ranges: &[],
        });
        // An sRGB canvas encodes what it is given: hand it linear colours so the gamma-space image survives.
        let fragment = if format.is_srgb() { "fs_to_linear" } else { "fs_copy" };
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("blit"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_fullscreen"),
                compilation_options: Default::default(),
                buffers: &[],
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some(fragment),
                compilation_options: Default::default(),
                targets: &[Some(wgpu::ColorTargetState { format, blend: None, write_mask: wgpu::ColorWrites::ALL })],
            }),
            primitive: Default::default(),
            depth_stencil: None,
            multisample: Default::default(),
            multiview: None,
            cache: None,
        });
        Self { layout, pipeline }
    }

    fn draw(&self, device: &wgpu::Device, queue: &wgpu::Queue, image: &wgpu::TextureView, target: &wgpu::TextureView) {
        let group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("blit"),
            layout: &self.layout,
            entries: &[wgpu::BindGroupEntry { binding: 0, resource: wgpu::BindingResource::TextureView(image) }],
        });
        let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor { label: Some("blit") });
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("blit"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: target,
                    resolve_target: None,
                    ops: wgpu::Operations { load: wgpu::LoadOp::Clear(wgpu::Color::BLACK), store: wgpu::StoreOp::Store },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &group, &[]);
            pass.draw(0..3, 0..1);
        }
        queue.submit([encoder.finish()]);
    }
}
