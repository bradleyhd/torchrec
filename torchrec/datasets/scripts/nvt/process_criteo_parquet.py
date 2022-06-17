#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import json
import os
import shutil
import time
from collections import OrderedDict
from typing import List
from nvtabular.utils import device_mem_size


import numpy as np
import nvtabular as nvt
from merlin.io import Shuffle
from utils.criteo_constant import (
    DAYS,
    DEFAULT_CAT_NAMES,
    DEFAULT_COLUMN_NAMES,
    DEFAULT_INT_NAMES,
    DEFAULT_LABEL_NAME,
    FREQUENCY_THRESHOLD,
    NUM_EMBEDDINGS_PER_FEATURE,
)
from utils.dask import setup_dask


def process_criteo(
    input_paths: List[str],
    output_path: str,
    num_embeddings_per_feature: List[int],
):
    part_mem_frac = 0.05
    device_size = device_mem_size(kind="total")
    part_size = int(part_mem_frac * device_size)
    cat_features = (
        DEFAULT_CAT_NAMES
        >> nvt.ops.FillMissing()
        >> nvt.ops.HashBucket(
            {
                cat_name: num_embeddings
                for cat_name, num_embeddings in zip(
                    DEFAULT_CAT_NAMES, num_embeddings_per_feature
                )
            }
        )
    )
    # We want to assign 0 to all missing values, and calculate log(x+3) for present values
    # so if we set missing values to -2, then the result of log(1+2+(-2)) would be 0
    cont_features = (
        DEFAULT_INT_NAMES
        >> nvt.ops.FillMissing()
        >> nvt.ops.LambdaOp(lambda col: col + 2)
        >> nvt.ops.LogOp()  # Log(1+x)
    )
    target_dtypes = {
        c: np.float32 for c in DEFAULT_COLUMN_NAMES[:14] + [DEFAULT_LABEL_NAME]
    }
    target_dtypes.update({c: "hex" for c in DEFAULT_COLUMN_NAMES[14:]})
    shuffle = Shuffle.PER_WORKER  # Shuffle algorithm
    out_files_per_proc = 8  # Number of output files per worker
    features = cat_features + cont_features + [DEFAULT_LABEL_NAME]
    workflow = nvt.Workflow(features)

    input_dataset = nvt.Dataset(input_paths, engine="parquet", part_size=part_size)
    workflow.fit(input_dataset)

    workflow.transform(input_dataset).to_parquet(
        output_path=os.path.join(output_path),
        dtypes=target_dtypes,
        cats=DEFAULT_CAT_NAMES,
        conts=DEFAULT_INT_NAMES,
        labels=[DEFAULT_LABEL_NAME],
        shuffle=shuffle,
        out_files_per_proc=out_files_per_proc,
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Preprocess criteo dataset")
    parser.add_argument("--base_path", "-b", dest="base_path", help="Base path")

    args = parser.parse_args()

    return args


if __name__ == "__main__":
    start_time = time.time()
    args = parse_args()

    dask_workdir = os.path.join(args.base_path, "dask_workdir")
    client = setup_dask(dask_workdir)

    output_path = os.path.join(args.base_path, "criteo_preproc")
    if os.path.exists(output_path):
        shutil.rmtree(output_path)
    os.makedirs(output_path)

    input_path = os.path.join(args.base_path, "criteo_parquet")
    assert os.path.exists(
        input_path
    ), f"Criteo parquet path {input_path} does not exist"

    out_train = os.path.join(output_path, "train")
    out_valid = os.path.join(output_path, "validation")
    out_test = os.path.join(output_path, "test")

    # train
    process_criteo(
        [os.path.join(input_path, f"day_{day}.parquet") for day in range(DAYS - 1)],
        out_train,
        NUM_EMBEDDINGS_PER_FEATURE,
    )

    # validation
    process_criteo(
        [os.path.join(input_path, "day_23.part0.parquet")],
        out_valid,
        NUM_EMBEDDINGS_PER_FEATURE,
    )

    # test
    process_criteo(
        [os.path.join(input_path, "day_23.part1.parquet")],
        out_test,
        NUM_EMBEDDINGS_PER_FEATURE,
    )

    print(f"Processing took {time.time()-start_time:.2f} sec")