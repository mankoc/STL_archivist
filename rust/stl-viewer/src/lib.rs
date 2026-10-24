//! The web client's model viewer: MeshPeek's renderer (`meshpeek-render`) drawing a scene the server sent
//! (`meshpeek_core::wire`) into a canvas, through WebGPU or WebGL2.
//!
//! The page owns the input: it turns pointer, wheel and key events into the camera moves below
//! (`web/src/viewer/input.ts`, MeshPeek's mouse mapping) and asks for a frame after each. Pixel arguments
//! are canvas pixels (CSS pixels times the device pixel ratio).
//!
//! Built with `npm run gen:viewer` (web/), which writes `web/src/viewer/wasm/`.

#![cfg(target_arch = "wasm32")]

use std::sync::Arc;

use glam::DVec3;
use meshpeek_core::{ObjectKind, wire};
use meshpeek_render::{Camera, GpuMesh, Material, Palette, Renderer, Style, wgpu};
use wasm_bindgen::prelude::*;

/// MeshPeek's default look: Plastic, in its model and support colours.
const PLASTIC: Material = Material { ambient: 0.1, diffuse: 0.8, specular: 0.3, specular_power: 20.0 };
const MODEL_COLOR: [u8; 3] = [0xc3, 0xcc, 0xd9];
const SUPPORT_COLOR: [u8; 3] = [0xe8, 0xa2, 0x4a];

fn style(kind: ObjectKind) -> Style {
    let color = match kind {
        ObjectKind::Model => MODEL_COLOR,
        ObjectKind::Support => SUPPORT_COLOR,
    };
    Style { color: color.map(|c| c as f32 / 255.0), material: PLASTIC }
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

/// What [`Viewer::load`] put in the view.
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

/// A canvas showing one scene.
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
        Ok(Viewer { device, queue, surface, config, renderer, camera: Camera::default(), blit, backend })
    }

    /// "WebGPU" or "WebGL2".
    #[wasm_bindgen(getter)]
    pub fn backend(&self) -> String {
        self.backend.clone()
    }

    /// Replace what is shown with a scene from `GET /api/models/{id}/view`, seen from the isometric view.
    pub fn load(&mut self, data: &[u8]) -> Result<Loaded, JsError> {
        let scene = wire::decode(data).map_err(js_error)?;
        self.renderer.clear();
        let max_buffer = self.device.limits().max_buffer_size;
        let mut triangles = 0;
        for object in &scene.objects {
            triangles += object.mesh.n_faces();
            // The renderer takes an Arc (MeshPeek shares meshes between threads); the browser has one thread.
            #[allow(clippy::arc_with_non_send_sync)]
            let mesh = Arc::new(GpuMesh::upload(&self.device, &object.mesh, max_buffer));
            self.renderer.add_object(&self.device, mesh, object.transform, style(object.kind), object.visible);
        }
        for plate in &scene.plates {
            self.renderer.add_plate(*plate);
        }
        self.show_view("isometric");
        Ok(Loaded { objects: scene.objects.len() as u32, triangles: triangles as f64, skipped: scene.skipped })
    }

    /// Follow the page's theme (background and plate colours).
    pub fn set_dark(&mut self, dark: bool) {
        self.renderer.set_palette(if dark { Palette::DARK } else { Palette::LIGHT });
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
