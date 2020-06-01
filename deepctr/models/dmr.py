# -*- coding:utf-8 -*-
"""
Author:
    Tingyi Tan,5636374@qq.com

Reference:
    [1] Ze Lyu, Yu Dong, Chengfu Huo, Weijun Ren. Deep Match to Rank Model for Personalized Click-Through Rate Prediction. https://github.com/lvze92/DMR
"""
import tensorflow as tf
from tensorflow.python.keras.layers import Dense, Concatenate, Flatten, Lambda
from tensorflow.python.keras.models import Model

from ..inputs import build_input_features, create_embedding_matrix, SparseFeat, VarLenSparseFeat, DenseFeat, embedding_lookup, get_dense_input, varlen_embedding_lookup, get_varlen_pooling_list, combined_dnn_input
from ..layers.core import DNN, PredictionLayer
from ..layers.sequence import AttentionSequencePoolingLayer
from ..layers.utils import concat_func, NoMask


def DMR(dnn_feature_columns,
        history_feature_list,
        dnn_use_bn=False,
        dnn_hidden_units=(200, 80),
        dnn_activation='relu',
        att_hidden_size=(80, 40),
        att_activation="dice",
        att_weight_normalization=False,
        l2_reg_dnn=0,
        l2_reg_embedding=1e-6,
        dnn_dropout=0,
        init_std=0.0001,
        seed=1024,
        task='binary'):
    """Instantiates the Deep Interest Network architecture.

    :param dnn_feature_columns: An iterable containing all the features used by deep part of the model.
    :param history_feature_list: list,to indicate  sequence sparse field
    :param dnn_use_bn: bool. Whether use BatchNormalization before activation or not in deep net
    :param dnn_hidden_units: list,list of positive integer or empty list, the layer number and units in each layer of deep net
    :param dnn_activation: Activation function to use in deep net
    :param att_hidden_size: list,list of positive integer , the layer number and units in each layer of attention net
    :param att_activation: Activation function to use in attention net
    :param att_weight_normalization: bool.Whether normalize the attention score of local activation unit.
    :param l2_reg_dnn: float. L2 regularizer strength applied to DNN
    :param l2_reg_embedding: float. L2 regularizer strength applied to embedding vector
    :param dnn_dropout: float in [0,1), the probability we will drop out a given DNN coordinate.
    :param init_std: float,to use as the initialize std of embedding vector
    :param seed: integer ,to use as random seed.
    :param task: str, ``"binary"`` for  binary logloss or  ``"regression"`` for regression loss
    :return: A Keras model instance.

    """

    features = build_input_features(dnn_feature_columns)

    sparse_feature_columns = list(
        filter(lambda x: isinstance(x, SparseFeat),
               dnn_feature_columns)) if dnn_feature_columns else []
    dense_feature_columns = list(
        filter(lambda x: isinstance(x, DenseFeat),
               dnn_feature_columns)) if dnn_feature_columns else []
    varlen_sparse_feature_columns = list(
        filter(lambda x: isinstance(x, VarLenSparseFeat),
               dnn_feature_columns)) if dnn_feature_columns else []

    history_feature_columns = []
    context_feature_columns = []
    context_dm_feature_columns = []
    sparse_varlen_feature_columns = []
    history_fc_names = list(map(lambda x: "his_" + x, history_feature_list))
    context_fc_names = list(map(lambda x: "cont_" + x, history_feature_list))
    context_dm_fc_names = list(map(lambda x: "cont_" + x + "_dm", history_feature_list))

    for fc in varlen_sparse_feature_columns:
        feature_name = fc.name
        if feature_name in history_fc_names:
            history_feature_columns.append(fc)
        elif feature_name in context_fc_names:
            context_feature_columns.append(fc)
        elif feature_name in context_dm_fc_names:
            context_dm_feature_columns.append(fc)
        else:
            sparse_varlen_feature_columns.append(fc)

    inputs_list = list(features.values())

    embedding_dict = create_embedding_matrix(dnn_feature_columns,
                                             l2_reg_embedding,
                                             init_std,
                                             seed,
                                             prefix="")

    query_emb_list = embedding_lookup(embedding_dict,
                                      features,
                                      sparse_feature_columns,
                                      history_feature_list,
                                      history_feature_list,
                                      to_list=True)
    keys_emb_list = embedding_lookup(embedding_dict,
                                     features,
                                     history_feature_columns,
                                     history_fc_names,
                                     history_fc_names,
                                     to_list=True)
    context_emb_list = embedding_lookup(embedding_dict,
                                        features,
                                        context_feature_columns,
                                        context_fc_names,
                                        context_fc_names,
                                        to_list=True)
    dnn_input_emb_list = embedding_lookup(embedding_dict,
                                          features,
                                          sparse_feature_columns,
                                          mask_feat_list=history_feature_list,
                                          to_list=True)
    dense_value_list = get_dense_input(features, dense_feature_columns)

    sequence_embed_dict = varlen_embedding_lookup(
        embedding_dict, features, sparse_varlen_feature_columns)
    sequence_embed_list = get_varlen_pooling_list(
        sequence_embed_dict,
        features,
        sparse_varlen_feature_columns,
        to_list=True)

    dnn_input_emb_list += sequence_embed_list

    keys_emb = concat_func(keys_emb_list, mask=True)
    context_emb = concat_func(context_emb_list, mask=True)
    query_emb = concat_func(query_emb_list, mask=True)

    hist, score = AttentionSequencePoolingLayer(
        att_hidden_size,
        att_activation,
        weight_normalization=att_weight_normalization,
        supports_masking=True,
        return_score=True,
        supports_context=True)([query_emb, keys_emb, context_emb])

    rel_i2i = Lambda(lambda x: tf.reshape(tf.reduce_sum(x, [1, 2]), [-1, 1, 1]))(score)

    deep_input_emb = concat_func(dnn_input_emb_list + [rel_i2i])

    deep_input_emb = Concatenate()([NoMask()(deep_input_emb), hist])
    deep_input_emb = Flatten()(deep_input_emb)
    dnn_input = combined_dnn_input([deep_input_emb], dense_value_list)
    output = DNN(dnn_hidden_units, dnn_activation, l2_reg_dnn, dnn_dropout,
                 dnn_use_bn, seed)(dnn_input)
    final_logit = Dense(1, use_bias=False)(output)

    output = PredictionLayer(task)(final_logit)

    model = Model(inputs=inputs_list, outputs=output)
    return model