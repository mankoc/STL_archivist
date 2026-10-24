// Copies the renderer's image (same size as the canvas) onto the canvas.

@group(0) @binding(0) var image: texture_2d<f32>;

@vertex
fn vs_fullscreen(@builtin(vertex_index) i: u32) -> @builtin(position) vec4<f32> {
    let xy = vec2<f32>(f32((i << 1u) & 2u), f32(i & 2u)) * 2.0 - 1.0;
    return vec4<f32>(xy, 0.0, 1.0);
}

fn texel(position: vec4<f32>) -> vec4<f32> {
    return textureLoad(image, vec2<i32>(position.xy), 0);
}

@fragment
fn fs_copy(@builtin(position) position: vec4<f32>) -> @location(0) vec4<f32> {
    return texel(position);
}

@fragment
fn fs_to_linear(@builtin(position) position: vec4<f32>) -> @location(0) vec4<f32> {
    let c = texel(position);
    return vec4<f32>(pow(c.rgb, vec3<f32>(2.2)), c.a);
}
