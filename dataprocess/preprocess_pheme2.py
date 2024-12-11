import logging
import os
import itertools
import re
from collections import Counter
import gensim
import sys
import numpy as np
import scipy.sparse as sp
import pickle
import jieba
import json

import torch
from transformers import BertTokenizer, BertModel, BertConfig

w2v_dim = 300
use_stopwords = False
max_len = 50
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

dic = {
    'non-rumor': 0,
    'false': 1,
    'unverified': 2,
    'true': 3,
}

stopwords_path = os.getcwd() + '/pheme_files/stopwords_eng1.txt'
stopwords_eng1 = []
with open(stopwords_path, 'r') as f:
    for line in f.readlines():
        stopwords_eng1.append(line.strip())

stopwords_path = os.getcwd() + '/pheme_files/stopwords_eng2.txt'
stopwords_eng2 = []
with open(stopwords_path, 'r') as f:
    for line in f.readlines():
        stopwords_eng2.append(line.strip())



def clean_str_cut(string, task):
    """
    Tokenization/string cleaning for all datasets except for SST.
    Original taken from https://github.com/yoonkim/CNN_sentence/blob/master/process_data.py
    """
    if "weibo" not in task:
        string = re.sub(r"[^A-Za-z0-9(),!?#@\'\`]", " ", string)
        string = re.sub(r"\'s", " \'s", string)
        string = re.sub(r"\'ve", " \'ve", string)
        string = re.sub(r"n\'t", " n\'t", string)
        string = re.sub(r"\'re", " \'re", string)
        string = re.sub(r"\'d", " \'d", string)
        string = re.sub(r"\'ll", " \'ll", string)

    string = re.sub(r",", " , ", string)
    string = re.sub(r"!", " ! ", string)
    string = re.sub(r"\(", " \( ", string)
    string = re.sub(r"\)", " \) ", string)
    string = re.sub(r"\?", " \? ", string)
    string = re.sub(r"\s{2,}", " ", string)

    words = list(
        jieba.cut(string.strip().lower(), cut_all=False)) if "weibo" in task else string.strip().lower().split()
    if use_stopwords:
        words = [w for w in words if w not in stopwords_eng2]
    return words

def clean_str_BERT(string):
    r1 = u'[a-zA-Z0-9’!"#$%&\'()*+,-./:;<=>?@，。?★、…【】《》？“”‘’！[\\]^_`{|}~]+'  # 用户也可以在此进行自定义过滤字符
    r2 = "[\s+\.\!\/_,$%^*(+\"\']+|[+——！，。？、~@#￥%……&*（）]+"
    r3 = "[.!//_,$&%^*()<>+\"'?@#-|:~{}]+|[——！\\\\，。=？、：“”‘’《》【】￥……（）]+"
    r4 = "\\【.*?】+|\\《.*?》+|\\#.*?#+|[.!/_,$&%^*()<>+""'?@|:~{}#]+|[——！\\\，。=？、：“”‘’￥……（）《》【】]"
    string = string.split('http')[0]
    cleanr = re.compile('<.*?>')
    string = re.sub(cleanr, ' ', string)
    # string = re.sub(r1, ' ', string)
    # string = re.sub(r2, ' ', string)
    # string = re.sub(r3, ' ', string)
    string = re.sub(r4, ' ', string)
    return string

def build_symmetric_adjacency_matrix(edges, shape):
    def normalize_adj(mx):
        """Row-normalize sparse matrix"""
        rowsum = np.array(mx.sum(1))
        r_inv_sqrt = np.power(rowsum, -0.5).flatten()
        r_inv_sqrt[np.isinf(r_inv_sqrt)] = 0.
        r_mat_inv_sqrt = sp.diags(r_inv_sqrt)
        return mx.dot(r_mat_inv_sqrt).transpose().dot(r_mat_inv_sqrt)
    adj = sp.coo_matrix(arg1=(edges[:, 2], (edges[:, 0], edges[:, 1])), shape=shape, dtype=np.float32)
    adj = adj + adj.T.multiply(adj.T > adj) - adj.multiply(adj.T > adj)
    adj = normalize_adj(adj + sp.eye(adj.shape[0]))
    return adj.tocoo()


def read_corpus(root_path, file_name):
    X_tids = []
    X_uids = []
    old_id_post_map = {}
    with open(root_path + file_name + ".train", 'r', encoding='utf-8') as input:
        X_train_tid, X_train_content, X_train_w2v, y_train = [], [], [], []
        for line in input.readlines():
            tid, content, label = line.strip().split("\t")
            X_tids.append(tid)
            X_train_tid.append(tid)
            fenci_res = clean_str_cut(content, file_name)
            X_train_content.append(clean_str_BERT(content))
            X_train_w2v.append(fenci_res)
            y_train.append(dic[label])
            old_id_post_map[tid] = fenci_res

    with open(root_path + file_name + ".dev", 'r', encoding='utf-8') as input:
        X_dev_tid, X_dev_content, X_dev_w2v, y_dev = [], [], [], []
        for line in input.readlines():
            tid, content, label = line.strip().split("\t")
            X_tids.append(tid)
            X_dev_tid.append(tid)
            fenci_res = clean_str_cut(content, file_name)
            X_dev_content.append(clean_str_BERT(content))
            X_dev_w2v.append(fenci_res)
            y_dev.append(dic[label])
            old_id_post_map[tid] = fenci_res

    with open(root_path + file_name + ".test", 'r', encoding='utf-8') as input:
        X_test_tid, X_test_content, X_test_w2v, y_test = [], [], [], []
        for line in input.readlines():
            tid, content, label = line.strip().split("\t")
            X_tids.append(tid)
            X_test_tid.append(tid)
            fenci_res = clean_str_cut(content, file_name)
            X_test_content.append(clean_str_BERT(content))
            X_test_w2v.append(fenci_res)
            y_test.append(dic[label])
            old_id_post_map[tid] = fenci_res

    with open(root_path + file_name + "_graph.txt", 'r', encoding='utf-8') as input:
        relation = []
        for line in input.readlines():
            tmp = line.strip().split()
            src = tmp[0]
            X_uids.append(src)

            for dst_ids_ws in tmp[1:]:
                dst, w = dst_ids_ws.split(":")
                X_uids.append(dst)
                relation.append([src, dst, w])

    with open(root_path + '/pheme_files/comment_content.json','r',encoding='utf-8') as input:
        old_id_comment_map = {}
        test_id_comment_map = json.load(input)
        for k,v in test_id_comment_map.items():
            fenci_res = clean_str_cut(v,file_name)
            old_id_comment_map[k] = fenci_res

    with open(root_path + '/pheme_files/user_tweet.json','r',encoding='utf-8') as input:
        old_user_post_map = {}
        old_user_post_map = json.load(input)

    X_id = list(set(X_tids + X_uids))
    num_node = len(X_id)
    X_id_dic = {id: i for i, id in enumerate(X_id)}
    with open(os.getcwd() + '/pheme_files/new_id_dic.json', 'w') as f:
        f.write(json.dumps(X_id_dic, indent=4))

    with open(root_path + "/pheme_files/original_adj", 'r', encoding='utf-8') as f:
        original_adj = {}
        original_adj_old = json.load(f)
        for i, v in original_adj_old.items():
            i = X_id_dic[i]
            original_adj[i] = []
            for j in v:
                j = X_id_dic[str(j)]
                original_adj[i].append(j)
    with open(root_path + "/pheme_files/original_adj", 'w', encoding='utf-8') as f:
        json.dump(original_adj, f)

    relation = np.array([[X_id_dic[tup[0]], X_id_dic[tup[1]], tup[2]] for tup in relation])
    relation = build_symmetric_adjacency_matrix(edges=relation, shape=(num_node, num_node))

    X_train_tid = np.array([X_id_dic[tid] for tid in X_train_tid])
    X_dev_tid = np.array([X_id_dic[tid] for tid in X_dev_tid])
    X_test_tid = np.array([X_id_dic[tid] for tid in X_test_tid])


    np.random.seed(666)
    model = gensim.models.KeyedVectors.load_word2vec_format(fname=os.getcwd()  + "/pheme_files/twitter_w2v.bin", binary=True)
    node_embedding_matrix = np.random.uniform(-0.25, 0.25, (num_node, 300))
    postnum,commentnum,usernum = 0,0,0
    for i, words in old_id_post_map.items():
        new_id = X_id_dic[i]
        embedding = 0.0
        count = 0
        for word in words:
            if model.__contains__(word):
                embedding += model[word]
                count += 1
        if count > 0:
            embedding = embedding / count
            node_embedding_matrix[new_id, :] = embedding
            postnum += 1
    for i, words in old_id_comment_map.items():
        new_id = X_id_dic[i]
        embedding = 0.0
        count = 0
        for word in words:
            if model.__contains__(word):
                embedding += model[word]
                count += 1
        if count > 0:
            embedding = embedding / count
            node_embedding_matrix[new_id, :] = embedding
            commentnum += 1
    for u,posts in old_user_post_map.items():
        new_uid = X_id_dic[u]
        embedding = 0.0
        count = 0
        for post in posts:
            new_pid = X_id_dic[post]
            embedding += node_embedding_matrix[new_pid,:]
            count += 1
        if count > 0:
            embedding = embedding / count
            node_embedding_matrix[new_uid,:] = embedding
            usernum += 1

    pickle.dump([node_embedding_matrix],
                open(root_path + "/pheme_files/node_embedding.pkl", 'wb'))

    return X_train_tid, X_train_content, X_train_w2v, y_train, \
           X_dev_tid, X_dev_content, X_dev_w2v, y_dev, \
           X_test_tid, X_test_content, X_test_w2v, y_test, \
           relation

def w2v_feature_extract(root_path, filename, w2v_path): # 数据格式
    X_train_tid, X_train, X_train_w2v ,y_train, \
    X_dev_tid, X_dev, X_dev_w2v, y_dev, \
    X_test_tid, X_test, X_test_w2v, y_test, relation = read_corpus(root_path, filename)
    # print("------x_train_tid------:", X_train_tid)
    # print("------x_train1------:", X_train)
    # print("------y_train------:", y_train)

    print("text word2vec generation.......")
    vocabulary, word_embeddings = build_vocab_word2vec(X_train_w2v + X_dev_w2v + X_test_w2v, w2v_path=w2v_path)
    pickle.dump(vocabulary, open(root_path + "/pheme_files/vocab.pkl", 'wb'))
    X_train_w2v = build_input_data(X_train_w2v, vocabulary)
    X_dev_w2v = build_input_data(X_dev_w2v, vocabulary)
    X_test_w2v = build_input_data(X_test_w2v, vocabulary)
    UNCASED = '../../bert-base-uncased/'
    VOCAB = 'vocab.txt'
    tokenizer = BertTokenizer.from_pretrained(os.path.join(UNCASED, VOCAB))
    X_train_bert = []
    temp = X_train
    train_attention_mask_bert = []
    idxmid = 0
    for mid in temp:
        text = temp[idxmid]
        # print(text)
        # BERT==without remove stop words
        # text = clean_str_BERT(text)
        # print('data_process text:',text)
        tokenizer_encoding = tokenizer.encode_plus(text, add_special_tokens=True, max_length=max_len, \
                                                   pad_to_max_length=True, padding='max_length',
                                                   truncation='only_first', \
                                                   return_attention_mask=True)
        # print('data_process tokenizer_encoding:',type(tokenizer_encoding['input_ids']))
        X_train_bert.append(tokenizer_encoding['input_ids'])
        train_attention_mask_bert.append(tokenizer_encoding['attention_mask'])
        # train_attention_mask_bert = tokenizer_encoding['attention_mask']
        # text_encoding = model(input_ids, attention_mask=attention_mask_bert)
        # X_train_bert.append(text_encoding['last_hidden_state'])
        idxmid = idxmid + 1
    # str = torch.tensor(str)
    X_dev_bert = []
    temp = X_dev
    dev_attention_mask_bert = []
    idxmid = 0
    for mid in temp:
        text = temp[idxmid]
        # print(text)
        # BERT==without remove stop words
        # text = clean_str_BERT(text)
        # print('data_process text:',text)
        tokenizer_encoding = tokenizer.encode_plus(text, add_special_tokens=True, max_length=max_len, \
                                                   pad_to_max_length=True, padding='max_length',
                                                   truncation='only_first', \
                                                   return_attention_mask=True)
        # print('data_process tokenizer_encoding:',type(tokenizer_encoding['input_ids']))
        X_dev_bert.append(tokenizer_encoding['input_ids'])
        dev_attention_mask_bert.append(tokenizer_encoding['attention_mask'])
        # dev_attention_mask_bert = tokenizer_encoding['attention_mask']
        # text_encoding = model(input_ids, attention_mask=attention_mask_bert)
        # X_dev_bert.append(text_encoding['last_hidden_state'])
        idxmid = idxmid + 1
    X_test_bert = []
    temp = X_test
    test_attention_mask_bert = []
    idxmid = 0
    for mid in temp:
        text = temp[idxmid]
        # print(text)
        # BERT==without remove stop words
        # text = clean_str_BERT(text)
        # print('data_process text:',text)
        tokenizer_encoding = tokenizer.encode_plus(text, add_special_tokens=True, max_length=max_len, \
                                                   pad_to_max_length=True, padding='max_length', truncation='only_first', \
                                                   return_attention_mask=True)
        # print('data_process tokenizer_encoding:',type(tokenizer_encoding['input_ids']))
        X_test_bert.append(tokenizer_encoding['input_ids'])
        test_attention_mask_bert.append(tokenizer_encoding['attention_mask'])
        # test_attention_mask_bert = tokenizer_encoding['attention_mask']
        # text_encoding = model(input_ids, attention_mask=attention_mask_bert)
        # X_test_bert.append(text_encoding['last_hidden_state'])
        idxmid = idxmid + 1
    # for i in X_train:
    #     X_train[i] = tokenizer(X_train[i], return_tensors='pt')
    # X_train = model(X_train)
    # X_dev = tokenizer(X_dev, return_tensors='pt')
    # X_dev = model(**X_dev)
    # X_test = tokenizer(X_test, return_tensors='pt')
    # X_test = model(**X_test)
    # print("------x_train------:", X_train_bert)
    # print("------attention_mask_bert------:", train_attention_mask_bert)
    # print("-----text-------:", X_train)
    pickle.dump([X_train_tid, X_train_w2v, X_train_bert, X_train, y_train, word_embeddings, relation, train_attention_mask_bert], open(root_path + "/pheme_files/train.pkl", 'wb'))
    pickle.dump([X_dev_tid, X_dev_w2v, X_dev_bert, X_dev, y_dev, dev_attention_mask_bert], open(root_path + "/pheme_files/dev.pkl", 'wb'))
    pickle.dump([X_test_tid, X_test_w2v, X_test_bert, X_test, y_test, test_attention_mask_bert], open(root_path + "/pheme_files/test.pkl", 'wb'))


def build_vocab_word2vec(sentences, w2v_path='numberbatch-en.txt'):
    """
    Builds a vocabulary mapping from word to index based on the sentences.
    Returns vocabulary mapping and inverse vocabulary mapping.
    """

    # Build vocabulary
    vocabulary_inv = []
    word_counts = Counter(itertools.chain(*sentences))
    vocabulary_inv += [x[0] for x in word_counts.most_common() if x[1] >= 1]
    vocabulary = {x: i for i, x in enumerate(vocabulary_inv)}

    print("embedding_weights generation.......")
    word2vec = vocab_to_word2vec(w2v_path, vocabulary)
    # 输出
    embedding_weights = build_word_embedding_weights(word2vec, vocabulary_inv)
    return vocabulary, embedding_weights

def vocab_to_word2vec(fname, vocab):
    """
    Load word2vec from Mikolov
    """
    np.random.seed(666)
    word_vecs = {}
    model = gensim.models.KeyedVectors.load_word2vec_format(fname, binary=True) # 模型
    count_missing = 0
    for word in vocab:
        if model.__contains__(word):
            word_vecs[word] = model[word]
        else:
            count_missing += 1
            word_vecs[word] = np.random.uniform(-0.25, 0.25, w2v_dim)
    return word_vecs


def build_word_embedding_weights(word_vecs, vocabulary_inv):
    vocab_size = len(vocabulary_inv)
    embedding_weights = np.zeros(shape=(vocab_size + 1, w2v_dim), dtype='float32')
    embedding_weights[0] = np.zeros(shape=(w2v_dim,))

    for idx in range(1, vocab_size):
        embedding_weights[idx] = word_vecs[vocabulary_inv[idx]]
    print("Embedding matrix of size " + str(np.shape(embedding_weights)))
    return embedding_weights

def build_input_data(X, vocabulary):
    """
    Maps sentencs and labels to vectors based on a vocabulary.
    """
    x = [[vocabulary[word] for word in sentence if word in vocabulary] for sentence in X]
    x = pad_sequence(x, max_len)
    return x

def pad_sequence(X, max_len):
    X_pad = []
    for doc in X:
        if len(doc) >= max_len:
            doc = doc[:max_len]
        else:
            doc = [0] * (max_len - len(doc)) + doc
        X_pad.append(doc)
    return X_pad


if __name__ == "__main__":
    # logging.basicConfig(level=logging.DEBUG,  # 控制台打印的日志级别
    #                     filename='61screenshot.log',
    #                     filemode='a',  ##模式，有w和a，w就是写模式，每次都会重新写日志，覆盖之前的日志
    #                     # a是追加模式，默认如果不写的话，就是追加模式
    #                     format=
    #                     '%(asctime)s - %(pathname)s[line:%(lineno)d] - %(levelname)s: %(message)s'
    #                     # 日志格式
    #                     )
    # temp = sys.stdout
    # f = open('61screenshot.log', 'a')
    # sys.stdout = f
    with open(os.getcwd() + '/preprocess_pheme.py') as f:
        exec(f.read())
    root_path = os.getcwd()
    filename = '/pheme_files/pheme'
    w2v_feature_extract(root_path=root_path, filename=filename, w2v_path=os.getcwd() + "/pheme_files/twitter_w2v.bin")
    f.close()

