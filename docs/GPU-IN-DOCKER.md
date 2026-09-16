# GPUs in Docker without nvidia-container-toolkit (and without root)

The SM120 runtimes for this model ship as container images. If `nvidia-ctk` is not
installed on your host and you cannot `apt install` it, the usual advice is a dead
end:

```
docker: Error response from daemon: could not select device driver ""
with capabilities: [[gpu]]
```

You do not need the toolkit. Membership in the `docker` group is enough. Pass the
device nodes through and bind-mount the host's driver libraries.

## The recipe

```bash
D=/usr/lib/x86_64-linux-gnu
V=595.84                     # your driver version: ls $D/libcuda.so.*

docker run --rm \
  --device /dev/nvidia0 --device /dev/nvidia1 --device /dev/nvidiactl \
  --device /dev/nvidia-uvm --device /dev/nvidia-uvm-tools \
  -v $D/libcuda.so.$V:/nvdrv/libcuda.so.1:ro \
  -v $D/libnvidia-ml.so.$V:/nvdrv/libnvidia-ml.so.1:ro \
  -v $D/libnvidia-nvvm.so.$V:/nvdrv/libnvidia-nvvm.so.4:ro \
  -v $D/libnvidia-ptxjitcompiler.so.$V:/nvdrv/libnvidia-ptxjitcompiler.so.1:ro \
  -e LD_LIBRARY_PATH=/nvdrv \
  your-image python -c \
  "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"
```

```
True 2
```

## Two details that cost us an hour

**Mount the libraries under their SONAME, not their real filename.** The host has
`libcuda.so.595.84` with `libcuda.so.1` as a symlink beside it. Bind-mounting the
versioned file to its own path inside the container does not help, because nothing
looks for `libcuda.so.595.84`. Mount it *as* `libcuda.so.1`. Creating the symlink
inside the container at runtime also failed for us; mounting under the target name
is simpler and works.

**`LD_LIBRARY_PATH` is mandatory.** Mounting into `/usr/lib/x86_64-linux-gnu`
inside the container is not enough on its own — the loader cache in the image does
not know about the newly appeared files, and `torch.cuda.is_available()` stays
`False` with `Found no NVIDIA driver on your system`. Put the libraries in their
own directory and point `LD_LIBRARY_PATH` at it. This was the difference between
"does not work at all" and "both cards visible, sm_120".

## Scope

This gets the driver API into the container. It does not give you MIG, MPS, device
enumeration niceties, or anything else the toolkit normally sets up — but for
running an inference engine it is sufficient. Adjust the device list to the cards
you want to expose; `CUDA_VISIBLE_DEVICES` inside the container still applies.
