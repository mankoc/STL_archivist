//! Draws the scene (gradient background, meshes, build plate, origin axes) into an offscreen texture.

use std::sync::Arc;

use bytemuck::{Pod, Zeroable};
use glam::{DMat4, DVec3};
use meshpeek_core::Plate;
use wgpu::util::DeviceExt;

use crate::{COLOR_FORMAT, Camera, GpuMesh, box_corners};

/// Depth buffer format.
const DEPTH_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Depth32Float;

/// Colours of everything that is not a model: background gradient, build plate and its outline.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Palette {
    /// Background colour at the top of the view.
    pub background_top: [f32; 3],
    /// Background colour at the bottom of the view.
    pub background_bottom: [f32; 3],
    /// RGB + opacity of the translucent plate floor.
    pub plate_fill: [f32; 4],
    /// RGB + opacity of the build-volume outline.
    pub plate_outline: [f32; 4],
}

impl Palette {
    /// Colours for the dark UI theme.
    pub const DARK: Palette = Palette {
        background_top: [0.294, 0.314, 0.353],    // #4b505a
        background_bottom: [0.145, 0.153, 0.169], // #25272b
        plate_fill: [0.478, 0.518, 0.580, 0.35],  // #7a8494
        plate_outline: [0.478, 0.518, 0.580, 0.6],
    };
    /// Colours for the light UI theme.
    pub const LIGHT: Palette = Palette {
        background_top: [0.965, 0.973, 0.984],     // #f6f8fb
        background_bottom: [0.780, 0.808, 0.851],  // #c7ced9
        plate_fill: [0.553, 0.600, 0.671, 0.30],   // #8d99ab
        plate_outline: [0.353, 0.400, 0.471, 0.7], // #5a6678
    };
}

/// Phong-style surface parameters (same meaning as VTK's).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Material {
    /// Share of the colour shown regardless of the lights.
    pub ambient: f32,
    /// Strength of the matte lighting.
    pub diffuse: f32,
    /// Strength of the white highlights.
    pub specular: f32,
    /// Sharpness of the highlights (higher = smaller, glossier).
    pub specular_power: f32,
}

/// How an object is drawn.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Style {
    /// Gamma-space RGB in 0..1, used when the mesh has no vertex colours.
    pub color: [f32; 3],
    /// Lighting response.
    pub material: Material,
}

/// Handle of an object added to the [`Renderer`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ObjectId(u64);

/// Per-frame shader constants (`Globals` in the shaders).
#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct GlobalsUniform {
    view: [[f32; 4]; 4],
    proj: [[f32; 4]; 4],
    background_top: [f32; 4],
    background_bottom: [f32; 4],
}

/// Per-object shader constants (`Object` in `mesh.wgsl`).
#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct ObjectUniform {
    model: [[f32; 4]; 4],
    color: [f32; 4],
    material: [f32; 4],
}

/// Vertex of the unlit overlays (plate floor, outlines, axes).
#[repr(C)]
#[derive(Clone, Copy, Pod, Zeroable)]
struct FlatVertex {
    position: [f32; 3],
    color: [f32; 4],
}

/// A drawn instance of a mesh: placement, style and its uniform buffer.
struct Object {
    mesh: Arc<GpuMesh>,
    model: DMat4,
    style: Style,
    visible: bool,
    uniform: wgpu::Buffer,
    bind_group: wgpu::BindGroup,
}

/// The offscreen images rendered into, recreated when the view size changes.
struct Target {
    size: [u32; 2],
    /// Multisampled colour image, resolved into `color` (absent without MSAA).
    msaa: Option<wgpu::TextureView>,
    color: wgpu::Texture,
    color_view: wgpu::TextureView,
    depth: wgpu::TextureView,
}

/// All render pipelines, created once.
struct Pipelines {
    /// Lit meshes in the object's colour.
    plain: wgpu::RenderPipeline,
    /// Lit meshes with vertex colours.
    colored: wgpu::RenderPipeline,
    wire: Option<(wgpu::RenderPipeline, wgpu::RenderPipeline)>, // (plain, colored) with PolygonMode::Line
    /// Plate outline and origin axes.
    lines: wgpu::RenderPipeline,
    /// Plate floor (blended, no depth writes).
    translucent: wgpu::RenderPipeline,
    /// Full-screen gradient.
    background: wgpu::RenderPipeline,
}

/// Draws the scene offscreen; the UI shows the returned texture. Only re-renders when asked to.
pub struct Renderer {
    sample_count: u32,
    globals: wgpu::Buffer,
    globals_group: wgpu::BindGroup,
    object_layout: wgpu::BindGroupLayout,
    pipelines: Pipelines,
    objects: Vec<(ObjectId, Object)>,
    next_id: u64,
    plates: Vec<Plate>,
    show_plates: bool,
    /// Draw the build volume (the box above the plate), not only the plate.
    show_volume: bool,
    lines: Option<(wgpu::Buffer, u32)>,
    floor: Option<(wgpu::Buffer, u32)>,
    overlays_dirty: bool,
    palette: Palette,
    target: Option<Target>,
    wireframe: bool,
    /// Something changed since the last render (the camera is compared separately by the caller).
    dirty: bool,
}

impl Renderer {
    /// `sample_count` is the MSAA level (1 disables it); use [`Renderer::best_sample_count`].
    pub fn new(device: &wgpu::Device, sample_count: u32) -> Self {
        let uniform_layout = |label| {
            device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
                label: Some(label),
                entries: &[wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                }],
            })
        };
        let globals_layout = uniform_layout("globals");
        let object_layout = uniform_layout("object");
        let globals = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("globals"),
            size: size_of::<GlobalsUniform>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        let globals_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("globals"),
            layout: &globals_layout,
            entries: &[wgpu::BindGroupEntry { binding: 0, resource: globals.as_entire_binding() }],
        });
        let pipelines = Pipelines::new(device, &globals_layout, &object_layout, sample_count);
        Self {
            sample_count,
            globals,
            globals_group,
            object_layout,
            pipelines,
            objects: Vec::new(),
            next_id: 0,
            plates: Vec::new(),
            show_plates: true,
            show_volume: true,
            lines: None,
            floor: None,
            overlays_dirty: true,
            palette: Palette::DARK,
            target: None,
            wireframe: false,
            dirty: true,
        }
    }

    /// Highest of 4x / 1x MSAA the adapter supports for our colour and depth formats.
    /// Software adapters (WARP, lavapipe) get none: MSAA multiplies their per-pixel CPU work.
    pub fn best_sample_count(adapter: &wgpu::Adapter) -> u32 {
        if adapter.get_info().device_type == wgpu::DeviceType::Cpu {
            return 1;
        }
        let ok = |format| adapter.get_texture_format_features(format).flags.sample_count_supported(4);
        if ok(COLOR_FORMAT) && ok(DEPTH_FORMAT) { 4 } else { 1 }
    }

    /// The adapter can draw triangle edges (`POLYGON_MODE_LINE`).
    pub fn supports_wireframe(&self) -> bool {
        self.pipelines.wire.is_some()
    }

    /// Background and plate colours (e.g. to follow the UI's light/dark theme).
    pub fn set_palette(&mut self, palette: Palette) {
        if self.palette != palette {
            self.palette = palette;
            self.overlays_dirty = true;
            self.dirty = true;
        }
    }

    /// The current colours.
    pub fn palette(&self) -> Palette {
        self.palette
    }

    /// Draw triangle edges instead of lit surfaces (ignored without wireframe support).
    pub fn set_wireframe(&mut self, on: bool) {
        self.wireframe = on && self.supports_wireframe();
        self.dirty = true;
    }

    /// Add a mesh with its object-to-world `model` matrix and style. One GPU mesh can back several objects.
    pub fn add_object(
        &mut self,
        device: &wgpu::Device,
        mesh: Arc<GpuMesh>,
        model: DMat4,
        style: Style,
        visible: bool,
    ) -> ObjectId {
        let uniform = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("object"),
            contents: bytemuck::bytes_of(&object_uniform(model, style)),
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        });
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("object"),
            layout: &self.object_layout,
            entries: &[wgpu::BindGroupEntry { binding: 0, resource: uniform.as_entire_binding() }],
        });
        let id = ObjectId(self.next_id);
        self.next_id += 1;
        self.objects.push((id, Object { mesh, model, style, visible, uniform, bind_group }));
        self.overlays_dirty = true;
        self.dirty = true;
        id
    }

    /// The object with this id, if it still exists.
    fn object(&mut self, id: ObjectId) -> Option<&mut Object> {
        self.objects.iter_mut().find(|(i, _)| *i == id).map(|(_, o)| o)
    }

    /// Show or hide an object.
    pub fn set_visible(&mut self, id: ObjectId, visible: bool) {
        if let Some(object) = self.object(id) {
            object.visible = visible;
        }
        self.dirty = true;
    }

    /// Move an object by `offset` (world units).
    pub fn translate(&mut self, queue: &wgpu::Queue, id: ObjectId, offset: DVec3) {
        if let Some(object) = self.object(id) {
            object.model = DMat4::from_translation(offset) * object.model;
            queue.write_buffer(&object.uniform, 0, bytemuck::bytes_of(&object_uniform(object.model, object.style)));
        }
        self.overlays_dirty = true; // the origin axes are sized from the scene
        self.dirty = true;
    }

    /// Change an object's colour or material.
    pub fn set_style(&mut self, queue: &wgpu::Queue, id: ObjectId, style: Style) {
        if let Some(object) = self.object(id) {
            if object.style != style {
                object.style = style;
                queue.write_buffer(&object.uniform, 0, bytemuck::bytes_of(&object_uniform(object.model, style)));
            }
        }
        self.dirty = true;
    }

    /// Remove all objects and plates.
    pub fn clear(&mut self) {
        self.objects.clear();
        self.plates.clear();
        self.overlays_dirty = true;
        self.dirty = true;
    }

    /// Add a build plate (identical plates from several files are drawn once).
    pub fn add_plate(&mut self, plate: Plate) {
        if !self.plates.contains(&plate) {
            self.plates.push(plate);
            self.overlays_dirty = true;
            self.dirty = true;
        }
    }

    /// Show or hide the build plates (floor and outline); the origin axes stay.
    pub fn set_show_plates(&mut self, show: bool) {
        if self.show_plates != show {
            self.show_plates = show;
            self.overlays_dirty = true;
            self.dirty = true;
        }
    }

    /// Show or hide the printer's build volume (the outline box above the plate); the plate itself stays.
    pub fn set_show_volume(&mut self, show: bool) {
        if self.show_volume != show {
            self.show_volume = show;
            self.overlays_dirty = true;
            self.dirty = true;
        }
    }

    /// The part of a plate that is drawn: the whole build volume, or only its floor.
    fn shown_box(&self, plate: &Plate) -> (DVec3, DVec3) {
        let (lo, hi) = plate_box(plate);
        if self.show_volume { (lo, hi) } else { (lo, DVec3::new(hi.x, hi.y, lo.z)) }
    }

    /// World-space bounds of one object.
    pub fn object_bounds(&self, id: ObjectId) -> Option<(DVec3, DVec3)> {
        let (_, object) = self.objects.iter().find(|(i, _)| *i == id)?;
        world_bounds(object)
    }

    /// World-space bounds of all visible objects and plates.
    pub fn scene_bounds(&self) -> Option<(DVec3, DVec3)> {
        let boxes = self.objects.iter().filter(|(_, o)| o.visible).filter_map(|(_, o)| world_bounds(o));
        let plates = self.plates.iter().filter(|_| self.show_plates).map(|p| self.shown_box(p));
        boxes.chain(plates).reduce(|(a0, a1), (b0, b1)| (a0.min(b0), a1.max(b1)))
    }

    /// Something changed since the last render, or the view is now `size` pixels.
    pub fn needs_render(&self, size: [u32; 2]) -> bool {
        self.dirty || self.target.as_ref().is_none_or(|t| t.size != size)
    }

    /// Render the scene at `size` pixels; returns the colour texture view (format [`COLOR_FORMAT`]).
    pub fn render(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        size: [u32; 2],
        camera: &Camera,
    ) -> &wgpu::TextureView {
        let size = [size[0].max(1), size[1].max(1)];
        if self.target.as_ref().is_none_or(|t| t.size != size) {
            self.target = Some(Target::new(device, size, self.sample_count));
        }
        if self.overlays_dirty {
            self.rebuild_overlays(device);
        }
        let (view, proj) = camera.matrices(size[0] as f64 / size[1] as f64, self.scene_bounds());
        let [t0, t1, t2] = self.palette.background_top;
        let [b0, b1, b2] = self.palette.background_bottom;
        let globals = GlobalsUniform {
            view: view.to_cols_array_2d(),
            proj: proj.to_cols_array_2d(),
            background_top: [t0, t1, t2, 1.0],
            background_bottom: [b0, b1, b2, 1.0],
        };
        queue.write_buffer(&self.globals, 0, bytemuck::bytes_of(&globals));

        let target = self.target.as_ref().unwrap();
        let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor { label: Some("scene") });
        {
            let (view, resolve) = match &target.msaa {
                Some(msaa) => (msaa, Some(&target.color_view)),
                None => (&target.color_view, None),
            };
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("scene"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view,
                    resolve_target: resolve,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                    view: &target.depth,
                    depth_ops: Some(wgpu::Operations { load: wgpu::LoadOp::Clear(1.0), store: wgpu::StoreOp::Discard }),
                    stencil_ops: None,
                }),
                timestamp_writes: None,
                occlusion_query_set: None,
            });
            pass.set_bind_group(0, &self.globals_group, &[]);
            pass.set_pipeline(&self.pipelines.background);
            pass.draw(0..3, 0..1);

            let wire = self.pipelines.wire.as_ref().filter(|_| self.wireframe);
            for (_, object) in self.objects.iter().filter(|(_, o)| o.visible) {
                let colored = object.mesh.has_colors;
                let pipeline = match (wire, colored) {
                    (Some((plain, _)), false) => plain,
                    (Some((_, colored)), true) => colored,
                    (None, false) => &self.pipelines.plain,
                    (None, true) => &self.pipelines.colored,
                };
                pass.set_pipeline(pipeline);
                pass.set_bind_group(1, &object.bind_group, &[]);
                for chunk in &object.mesh.chunks {
                    pass.set_vertex_buffer(0, chunk.vertices.slice(..));
                    if let Some(colors) = &chunk.colors {
                        pass.set_vertex_buffer(1, colors.slice(..));
                    }
                    pass.set_index_buffer(chunk.indices.slice(..), wgpu::IndexFormat::Uint32);
                    pass.draw_indexed(0..chunk.index_count, 0, 0..1);
                }
            }
            if let Some((buffer, count)) = &self.lines {
                pass.set_pipeline(&self.pipelines.lines);
                pass.set_vertex_buffer(0, buffer.slice(..));
                pass.draw(0..*count, 0..1);
            }
            if let Some((buffer, count)) = &self.floor {
                pass.set_pipeline(&self.pipelines.translucent);
                pass.set_vertex_buffer(0, buffer.slice(..));
                pass.draw(0..*count, 0..1);
            }
        }
        queue.submit([encoder.finish()]);
        self.dirty = false;
        &self.target.as_ref().unwrap().color_view
    }

    /// The last rendered image as tightly packed RGBA rows (for tests and screenshots).
    pub fn read_pixels(&self, device: &wgpu::Device, queue: &wgpu::Queue) -> Option<(Vec<u8>, [u32; 2])> {
        let target = self.target.as_ref()?;
        let [w, h] = target.size;
        let padded = (w * 4).div_ceil(256) * 256;
        let buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("readback"),
            size: (padded * h) as u64,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor { label: Some("readback") });
        encoder.copy_texture_to_buffer(
            target.color.as_image_copy(),
            wgpu::TexelCopyBufferInfo {
                buffer: &buffer,
                layout: wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(padded), rows_per_image: None },
            },
            wgpu::Extent3d { width: w, height: h, depth_or_array_layers: 1 },
        );
        queue.submit([encoder.finish()]);
        buffer.slice(..).map_async(wgpu::MapMode::Read, |_| {});
        device.poll(wgpu::PollType::Wait).ok()?;
        let data = buffer.slice(..).get_mapped_range();
        let pixels = data.chunks(padded as usize).flat_map(|row| &row[..(w * 4) as usize]).copied().collect();
        Some((pixels, [w, h]))
    }

    /// Plate floor, build-volume outline and origin axes (sized from the scene).
    fn rebuild_overlays(&mut self, device: &wgpu::Device) {
        self.overlays_dirty = false;
        let mut lines = Vec::new();
        let mut floor = Vec::new();
        for plate in self.plates.iter().filter(|_| self.show_plates) {
            let (lo, hi) = plate_box(plate);
            let outline = self.palette.plate_outline;
            let corners = box_corners(lo, hi);
            let corner = |i: usize| corners[i].as_vec3().to_array();
            // the floor's outline (corners 0-3), then the volume's top and vertical edges
            let floor_edges = [(0, 1), (2, 3), (0, 2), (1, 3)];
            let volume_edges = [(4, 5), (6, 7), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7)];
            let edges = floor_edges.iter().chain(volume_edges.iter().filter(|_| self.show_volume));
            for &(a, b) in edges {
                lines.push(FlatVertex { position: corner(a), color: outline });
                lines.push(FlatVertex { position: corner(b), color: outline });
            }
            let fill = self.palette.plate_fill;
            for i in [0, 1, 3, 0, 3, 2] {
                floor.push(FlatVertex { position: corner(i), color: fill });
            }
        }
        let size = self.scene_bounds().map_or(10.0, |(lo, hi)| (hi - lo).max_element() * 0.08).max(2.0) as f32;
        for (axis, color) in [
            ([1.0, 0.0, 0.0], [0.9, 0.2, 0.2, 1.0]),
            ([0.0, 1.0, 0.0], [0.2, 0.8, 0.2, 1.0]),
            ([0.0, 0.0, 1.0], [0.3, 0.4, 1.0, 1.0]),
        ] {
            lines.push(FlatVertex { position: [0.0; 3], color });
            lines.push(FlatVertex { position: axis.map(|v: f32| v * size), color });
        }
        let upload = |vertices: &[FlatVertex]| {
            (!vertices.is_empty()).then(|| {
                let buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                    label: Some("overlay"),
                    contents: bytemuck::cast_slice(vertices),
                    usage: wgpu::BufferUsages::VERTEX,
                });
                (buffer, vertices.len() as u32)
            })
        };
        self.lines = upload(&lines);
        self.floor = upload(&floor);
    }
}

/// The uniform contents for an object.
fn object_uniform(model: DMat4, style: Style) -> ObjectUniform {
    let m = style.material;
    ObjectUniform {
        model: model.as_mat4().to_cols_array_2d(),
        color: [style.color[0], style.color[1], style.color[2], 1.0],
        material: [m.ambient, m.diffuse, m.specular, m.specular_power],
    }
}

/// World-space bounds of an object: its object-space box transformed (a box around the turned box).
fn world_bounds(object: &Object) -> Option<(DVec3, DVec3)> {
    let (lo, hi) = object.mesh.bounds?;
    let corners = box_corners(lo.as_dvec3(), hi.as_dvec3()).map(|c| object.model.transform_point3(c));
    Some(corners.iter().fold((corners[0], corners[0]), |(a, b), &p| (a.min(p), b.max(p))))
}

/// The build volume of a plate as a box.
fn plate_box(plate: &Plate) -> (DVec3, DVec3) {
    let [sx, sy, sz] = plate.size;
    let [px, py, pz] = plate.position;
    (DVec3::new(px - sx / 2.0, py - sy / 2.0, pz), DVec3::new(px + sx / 2.0, py + sy / 2.0, pz + sz))
}

impl Target {
    /// Images of `size` pixels; the colour one can be sampled by the UI and copied for screenshots.
    fn new(device: &wgpu::Device, size: [u32; 2], sample_count: u32) -> Self {
        let texture = |label, format, samples, usage| {
            device.create_texture(&wgpu::TextureDescriptor {
                label: Some(label),
                size: wgpu::Extent3d { width: size[0], height: size[1], depth_or_array_layers: 1 },
                mip_level_count: 1,
                sample_count: samples,
                dimension: wgpu::TextureDimension::D2,
                format,
                usage,
                view_formats: &[],
            })
        };
        let color = texture(
            "scene colour",
            COLOR_FORMAT,
            1,
            wgpu::TextureUsages::RENDER_ATTACHMENT
                | wgpu::TextureUsages::TEXTURE_BINDING
                | wgpu::TextureUsages::COPY_SRC,
        );
        let msaa = (sample_count > 1).then(|| {
            texture("scene msaa", COLOR_FORMAT, sample_count, wgpu::TextureUsages::RENDER_ATTACHMENT)
                .create_view(&Default::default())
        });
        let depth = texture("scene depth", DEPTH_FORMAT, sample_count, wgpu::TextureUsages::RENDER_ATTACHMENT)
            .create_view(&Default::default());
        let color_view = color.create_view(&Default::default());
        Self { size, msaa, color, color_view, depth }
    }
}

impl Pipelines {
    /// Compile the shaders and build every pipeline for `sample_count` MSAA.
    fn new(
        device: &wgpu::Device,
        globals_layout: &wgpu::BindGroupLayout,
        object_layout: &wgpu::BindGroupLayout,
        sample_count: u32,
    ) -> Self {
        let mesh_shader = device.create_shader_module(wgpu::include_wgsl!("shaders/mesh.wgsl"));
        let flat_shader = device.create_shader_module(wgpu::include_wgsl!("shaders/flat.wgsl"));
        let mesh_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("mesh"),
            bind_group_layouts: &[globals_layout, object_layout],
            push_constant_ranges: &[],
        });
        let flat_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("flat"),
            bind_group_layouts: &[globals_layout],
            push_constant_ranges: &[],
        });
        let position = [wgpu::VertexBufferLayout {
            array_stride: 12,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &wgpu::vertex_attr_array![0 => Float32x3],
        }];
        let colors = wgpu::VertexBufferLayout {
            array_stride: 4,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &wgpu::vertex_attr_array![1 => Unorm8x4],
        };
        let position_and_color = [position[0].clone(), colors];
        let flat_vertex = [wgpu::VertexBufferLayout {
            array_stride: size_of::<FlatVertex>() as u64,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x4],
        }];
        let depth = |write: bool, compare| wgpu::DepthStencilState {
            format: DEPTH_FORMAT,
            depth_write_enabled: write,
            depth_compare: compare,
            stencil: wgpu::StencilState::default(),
            bias: wgpu::DepthBiasState::default(),
        };
        let multisample = wgpu::MultisampleState { count: sample_count, ..Default::default() };
        let pipeline = |label,
                        layout: &wgpu::PipelineLayout,
                        shader: &wgpu::ShaderModule,
                        vs: &str,
                        fs: &str,
                        buffers: &[wgpu::VertexBufferLayout],
                        topology,
                        polygon_mode,
                        depth_stencil,
                        blend| {
            device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
                label: Some(label),
                layout: Some(layout),
                vertex: wgpu::VertexState {
                    module: shader,
                    entry_point: Some(vs),
                    compilation_options: Default::default(),
                    buffers,
                },
                fragment: Some(wgpu::FragmentState {
                    module: shader,
                    entry_point: Some(fs),
                    compilation_options: Default::default(),
                    targets: &[Some(wgpu::ColorTargetState {
                        format: COLOR_FORMAT,
                        blend,
                        write_mask: wgpu::ColorWrites::ALL,
                    })],
                }),
                primitive: wgpu::PrimitiveState { topology, polygon_mode, ..Default::default() },
                depth_stencil: Some(depth_stencil),
                multisample,
                multiview: None,
                cache: None,
            })
        };
        use wgpu::{CompareFunction as Cmp, PolygonMode, PrimitiveTopology as Topo};
        let mesh = |label, vs, fs, buffers: &[wgpu::VertexBufferLayout], mode| {
            pipeline(
                label,
                &mesh_layout,
                &mesh_shader,
                vs,
                fs,
                buffers,
                Topo::TriangleList,
                mode,
                depth(true, Cmp::Less),
                None,
            )
        };
        let wire = device.features().contains(wgpu::Features::POLYGON_MODE_LINE).then(|| {
            (
                mesh("wire", "vs_plain", "fs_wire", &position, PolygonMode::Line),
                mesh("wire colored", "vs_colored", "fs_wire", &position_and_color, PolygonMode::Line),
            )
        });
        let alpha = Some(wgpu::BlendState::ALPHA_BLENDING);
        Self {
            plain: mesh("mesh", "vs_plain", "fs_lit", &position, PolygonMode::Fill),
            colored: mesh("mesh colored", "vs_colored", "fs_lit", &position_and_color, PolygonMode::Fill),
            wire,
            lines: pipeline(
                "lines",
                &flat_layout,
                &flat_shader,
                "vs_flat",
                "fs_flat",
                &flat_vertex,
                Topo::LineList,
                PolygonMode::Fill,
                depth(true, Cmp::LessEqual),
                alpha,
            ),
            translucent: pipeline(
                "translucent",
                &flat_layout,
                &flat_shader,
                "vs_flat",
                "fs_flat",
                &flat_vertex,
                Topo::TriangleList,
                PolygonMode::Fill,
                depth(false, Cmp::LessEqual),
                alpha,
            ),
            background: pipeline(
                "background",
                &flat_layout,
                &flat_shader,
                "vs_background",
                "fs_flat",
                &[],
                Topo::TriangleList,
                PolygonMode::Fill,
                depth(false, Cmp::Always),
                None,
            ),
        }
    }
}
