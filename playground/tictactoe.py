"""Крестики-нолики: игрок (X) против компьютера (O)."""

import random


def print_board(board):
    """Отрисовка игрового поля."""
    print()
    for i in range(3):
        row = []
        for j in range(3):
            cell = board[i * 3 + j]
            row.append(cell if cell else str(i * 3 + j + 1))
        print(f" {row[0]} | {row[1]} | {row[2]} ")
        if i < 2:
            print("---+---+---")
    print()


def check_winner(board, player):
    """Проверяет, выиграл ли player."""
    wins = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),  # строки
        (0, 3, 6), (1, 4, 7), (2, 5, 8),  # столбцы
        (0, 4, 8), (2, 4, 6),              # диагонали
    ]
    return any(board[a] == board[b] == board[c] == player for a, b, c in wins)


def get_empty(board):
    """Возвращает список свободных клеток."""
    return [i for i in range(9) if board[i] is None]


def ai_move(board):
    """Простой AI: победить > заблокировать > центр > угол > край."""
    empty = get_empty(board)

    # Попытка выиграть
    for i in empty:
        board[i] = "O"
        if check_winner(board, "O"):
            board[i] = None
            return i
        board[i] = None

    # Блокировка победы игрока
    for i in empty:
        board[i] = "X"
        if check_winner(board, "X"):
            board[i] = None
            return i
        board[i] = None

    # Центр
    if 4 in empty:
        return 4

    # Углы
    corners = [i for i in [0, 2, 6, 8] if i in empty]
    if corners:
        return random.choice(corners)

    # Любая свободная
    return random.choice(empty)


def play():
    """Основной игровой цикл."""
    board = [None] * 9
    print("=== Крестики-нолики ===")
    print("Ты играешь за X. Вводи номер клетки (1-9).")
    print_board(board)

    while True:
        # Ход игрока
        while True:
            try:
                move = int(input("Твой ход: ")) - 1
                if move < 0 or move > 8:
                    print("Введи число от 1 до 9!")
                elif board[move] is not None:
                    print("Клетка занята!")
                else:
                    break
            except ValueError:
                print("Введи число от 1 до 9!")

        board[move] = "X"

        if check_winner(board, "X"):
            print_board(board)
            print("🎉 Ты победил!")
            break

        if not get_empty(board):
            print_board(board)
            print("Ничья!")
            break

        # Ход компьютера
        comp = ai_move(board)
        board[comp] = "O"
        print(f"Компьютер ходит в клетку {comp + 1}")

        if check_winner(board, "O"):
            print_board(board)
            print("Компьютер победил! Попробуй ещё раз.")
            break

        if not get_empty(board):
            print_board(board)
            print("Ничья!")
            break

        print_board(board)


if __name__ == "__main__":
    while True:
        play()
        again = input("\nСыграть ещё раз? (д/н): ").lower()
        if again not in ("д", "y", "да", "yes"):
            print("До встречи!")
            break
