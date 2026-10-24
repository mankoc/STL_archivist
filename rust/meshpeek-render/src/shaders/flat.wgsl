// Unlit coloured geometry (plate floor, outlines, axes) and the gradient background.

struct Globals {
    view: mat4x4<f32>,
    proj: mat4x4<f32>,
    background_top: vec4<f32>,
    background_bottom: vec4<f32>,
};
@group(0) @binding(0) var<uniform> globals: Globals;

struct VertexOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) color: vec4<f32>,
};

@vertex
fn vs_flat(@location(0) position: vec3<f32>, @location(1) color: vec4<f32>) -> VertexOut {
    var out: VertexOut;
    out.clip = globals.proj * globals.view * vec4<f32>(position, 1.0);
    out.color = color;
    return out;
}

@fragment
fn fs_flat(in: VertexOut) -> @location(0) vec4<f32> {
    return in.color;
}

// Full-screen triangle with a vertical gradient (colours from the palette), drawn first.

@vertex
fn vs_background(@builtin(vertex_index) i: u32) -> VertexOut {
    let xy = vec2<f32>(f32((i << 1u) & 2u), f32(i & 2u)) * 2.0 - 1.0;
    var out: VertexOut;
    out.clip = vec4<f32>(xy, 1.0, 1.0);
    out.color = mix(globals.background_bottom, globals.background_top, xy.y * 0.5 + 0.5);
    return out;
}
