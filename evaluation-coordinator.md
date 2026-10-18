# 장 종료 후 평가·개선 연결

workflows/COORDINATOR.md를 따른다. flow.py --start mock으로 회차를 만들고
evaluation 작업만 발급해 등록된 evaluation Work에 전달한다.
평가 Work는 최근 완료된 장중 회차의 불변 원본·세 응답·계산·결정·실제 관측을
읽고 workflows/EVALUATION.md대로 평가한다. 다른 회차를 같은 판단 입력으로 섞지 않는다.
evaluation-summary.json과 evaluation-report.md를 확인하고 회차를 종료한다.
주 1개 이내 개선 가설·비교·반증 조건을 기록한다. 운영 코드·고정 제한을 바꾸거나 주문하지 않는다.
성과가 없으면 '미입증'이다. 비교 자료가 없으면 개선 완료·실험 통과로 표시하지 않는다.
