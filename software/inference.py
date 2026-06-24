import json
import random
from pathlib import Path

import cv2
import numpy as np
import onnx
import torch
import tqdm

NUM_GPUS = 4
WORKERS = 4
ONNX_BATCH_SIZE = 10

CUSTOM_LABEL = 'NOISE'


class Dataset(torch.utils.data.Dataset):
    def __init__(self, paths):
        self.paths = paths

    def __getitem__(self, index):
        path = self.paths[index]

        img = cv2.imread(path)

        return path, img

    def __len__(self):
        return len(self.paths)


def parallel(
    func,
    inputs,
    outputs,
    workers=WORKERS,
    assignment=False,
    threaded=False,
    quiet=True,
    desc=None,
):
    import concurrent.futures

    import tqdm

    if len(inputs) == 0:
        return None

    if threaded:
        executor_cls = concurrent.futures.ThreadPoolExecutor
    else:
        executor_cls = concurrent.futures.ProcessPoolExecutor

    workers = min(len(inputs), workers)

    with tqdm.tqdm(total=len(inputs), disable=quiet, desc=desc) as progress:
        with executor_cls(max_workers=workers) as executor:
            futures = [executor.submit(func, input_) for input_ in inputs]
            for future in concurrent.futures.as_completed(futures):
                key, data = future.result()
                if key is None:
                    outputs.update(data)
                elif assignment:
                    outputs[int(key)] = data
                else:
                    if key not in outputs:
                        outputs[key] = {}
                    outputs[key].update(data)
                progress.update(1)


def worker(inputs):
    import onnxruntime as ort

    position, paths, onnx_filename, device_id = inputs

    def _collate_func(batch):
        paths = [item[0] for item in batch]
        imgs = np.array([item[1] for item in batch])
        return paths, imgs

    dataset = Dataset(paths)
    dataloader = torch.utils.data.DataLoader(
        dataset,
        batch_size=1,
        num_workers=WORKERS,
        collate_fn=_collate_func,
    )

    session = ort.InferenceSession(
        onnx_filename,
        providers=['CPUExecutionProvider'],
    )

    chunksize = ONNX_BATCH_SIZE
    predictions = {}
    for paths, inputs in tqdm.tqdm(
        dataloader, desc=f'Device: {device_id}', position=position
    ):
        b, h, w, c = inputs.shape
        assert len(paths) == 1
        assert b == 1

        ratio_y = 224 / h
        ratio_x = ratio_y * 0.5
        raw = cv2.resize(
            inputs[0], None, fx=ratio_x, fy=ratio_y, interpolation=cv2.INTER_LANCZOS4
        )

        inputs_ = []
        h, w, c = raw.shape
        if w <= h:
            canvas = np.zeros((h, h + 1, 3), dtype=raw.dtype)
            canvas[:, :w, :] = raw
            raw = canvas
            h, w, c = raw.shape

        for index in range(0, w - h, 100):
            inputs_.append(raw[:, index : index + h, :])
        inputs_ = np.array(inputs_)

        chunks = np.array_split(inputs_, np.arange(chunksize, len(inputs_), chunksize))
        outputs = []
        for chunk in chunks:
            outputs_ = session.run(
                None,
                {'input': chunk},
            )
            outputs.append(outputs_[0])
        outputs = np.vstack(outputs)
        outputs = outputs.mean(axis=0)
        predictions.update(dict(zip(paths, [outputs])))

    return None, predictions


print('Running inference')

predictions_onnx_model = 'model.mobilenet.onnx'

paths = Path('examples.timing.output').rglob('*.jpg')
paths = [str(path.absolute()) for path in paths]

random.shuffle(paths)
model = onnx.load(predictions_onnx_model)
mapping = json.loads(model.metadata_props[0].value)

print('\trunning inference on tiles')
chunks = np.array_split(paths, NUM_GPUS)

inputs = [
    (index, chunk.tolist(), predictions_onnx_model, index % NUM_GPUS)
    for index, chunk in enumerate(chunks)
]
predictions = {}
parallel(
    worker,
    inputs,
    predictions,
    quiet=True,
)

custom_index = mapping['backward'].get(CUSTOM_LABEL, None)
confs = np.array([predictions[path] for path in paths])
preds = np.argmax(confs, axis=1)

labels = list(range(len(mapping['forward'])))
display = [mapping['forward'][str(index)] for index in range(len(mapping['forward']))]

for path, pred, conf in zip(paths, preds, confs):
    print(path, display[pred], conf[pred])
