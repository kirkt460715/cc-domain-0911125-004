import math


def gain(grade):
    return 2 ** grade - 1


def discount(rank):
    return 1.0 / math.log2(rank + 1)


def evaluate(ranked, judgments, k):
    '''Grade a deduplicated unit ranking against frozen judgments.

    ranked is a list of {'unit', 'document_id', 'detail'} in rank order and
    judgments maps information unit to grade. Unjudged units contribute zero
    gain but are never counted as relevant: recall is measured over judged
    relevant units only, and coverage reports how much of the displayed
    ranking carries a judgment. Metrics that would divide by an empty ideal
    gain or by zero known relevant units stay None (not computable).
    '''
    entries = []
    dcg = 0.0
    for rank, item in enumerate(ranked[:k], start=1):
        grade = judgments.get(item['unit'])
        judged = grade is not None
        value = gain(grade) if judged else 0
        weight = discount(rank)
        dcg += value * weight
        entries.append({'rank': rank, 'unit': item['unit'], 'document_id': item['document_id'], 'detail': item['detail'], 'judged': judged, 'grade': grade if judged else None, 'gain': value, 'discount': weight})
    ideal = sorted((grade for grade in judgments.values() if grade > 0), reverse=True)
    idcg = sum(gain(grade) * discount(rank) for rank, grade in enumerate(ideal[:k], start=1))
    relevant = {unit for unit, grade in judgments.items() if grade >= 1}
    retrieved = {item['unit'] for item in ranked[:k]}
    judged_entries = sum(1 for entry in entries if entry['judged'])
    return {'ranking': entries, 'retrieved_units': len(ranked), 'dcg': dcg, 'idcg': idcg, 'ndcg': dcg / idcg if idcg > 0 else None, 'recall': len(relevant & retrieved) / len(relevant) if relevant else None, 'coverage': judged_entries / len(entries) if entries else None, 'judged_relevant': len(relevant), 'judged_units': len(judgments)}


def mean(values):
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None
