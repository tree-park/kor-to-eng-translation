import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from tqdm import tqdm
import math

from lib.data_batchify import TrainCorpus, collate_fn
from lib.data_preprocess import preprocessor


@torch.no_grad()
def accuracy(pred, target):
    acc = sum(pred.argmax(1) == target).item() / len(target)
    return acc


class LangTranslator:
    def __init__(self, model, ko_vocab, en_vocab, dconf, mconf, device):
        self.dconf = dconf
        self.mconf = mconf

        self.ko_vocab = ko_vocab
        self.en_vocab = en_vocab
        self.dataset = None
        self.dataload = None

        self.device = device
        self.model = model
        self.loss = nn.CrossEntropyLoss(ignore_index=0).to(self.device)
        self.optim = optim.Adam(params=self.model.parameters(), lr=self.mconf.lr)
        self.lrscheder = optim.lr_scheduler.ReduceLROnPlateau(self.optim, patience=5)

    def train(self, ko_corpus, en_corpus):
        train_set = self.trainset_form(ko_corpus, en_corpus, self.ko_vocab, self.en_vocab)
        self.dataset = TrainCorpus(train_set)
        self.dataload = DataLoader(self.dataset,
                                   batch_size=self.mconf.batch_size,
                                   num_workers=0, collate_fn=collate_fn)
        self.mconf.ko_size, self.mconf.en_size = len(self.ko_vocab) + 1, len(self.en_vocab) + 1

        total_loss = 0
        total_acc = 0
        self.model.train()
        max_step = self.mconf.step
        step = 0
        while True:
            for i, batch in tqdm(enumerate(self.dataload), desc="step", total=len(self.dataload)):
                step += 1
                ko, en = map(lambda ds: ds.to(self.device), batch)
                self.optim.zero_grad()
                pred = self.model(ko, en[:, :-1])
                pred, target = pred.contiguous().view(-1, pred.shape[2]), en[:, 1:].contiguous().reshape(1, -1).squeeze(0)
                b_loss = self.loss(pred, target)
                b_loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1)
                self.optim.step()

                total_acc += accuracy(pred, target)
                total_loss += b_loss.item()

                del ko, en, target, pred
                torch.cuda.empty_cache()

            itersize = math.ceil(len(self.dataset) / self.mconf.batch_size)
            ppl = math.exp(total_loss / itersize)
            print(step, total_loss, total_acc / itersize, ppl)
            self.lrscheder.step(total_loss)
            total_loss = 0
            self.en_vocab.to_idx2word()

            if step == max_step:
                break

    def trainset_form(self, ko_corpus, en_corpus, ko_vocab, en_vocab):
        """ form train data - word to idx """
        rst = []
        for ko, en in zip(ko_corpus, en_corpus):
            ko = [ko_vocab[x] for x in ko]
            en = [en_vocab[x] for x in en]
            rst.append([ko, en])
        return rst

    def predset_form(self, corpus, vocab):
        """ form evaluate data - word to idx """
        rst = []
        for ko in corpus:
            ko = [vocab[x] for x in ko]
            rst.append(ko)
        return rst

    def predict(self, corpus):
        """ predict trained model """
        ko_corpus = preprocessor(corpus, lang='ko')
        pred_set = self.predset_form(ko_corpus, self.ko_vocab)
        pred_set = [torch.tensor(data) for data in pred_set]
        dataset = torch.nn.utils.rnn.pad_sequence(pred_set, batch_first=True).to(self.device)
        pred = self.model.predict(dataset, maxlen=dataset.size(1))
        return pred

    def translate(self, kor: list):
        """ Translate Korean to English """
        pred = self.predict(kor)
        rst = []
        for sent_idx in pred:
            sent = [self.en_vocab.get_word(idx) for idx in sent_idx if not 0]
            rst.append(sent)
        return rst

    def save(self, fname: str):
        """ save model """

        torch.save({
            'model': self.model.state_dict(),
            'optim': self.optim.state_dict(),
            'ko_vocab': self.ko_vocab,
            'en_vocab': self.en_vocab
        }, 'results/model/' + fname)

    def load(self, fname: str, retrain=False):
        """ load model """
        if not self.model:
            raise
        checkpoint = torch.load('results/model/' + fname)
        self.model.load_state_dict(checkpoint['model'])
        if self.optim and retrain:
            self.optim.load_state_dict(checkpoint['optim'])
        self.ko_vocab = checkpoint['ko_vocab']
        self.en_vocab = checkpoint['en_vocab']
        self.en_vocab.to_idx2word()
        self.model.eval()
        print(len(self.ko_vocab), len(self.en_vocab))

