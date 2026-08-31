from datetime import datetime


def get_system_time():
    """
    Devuelve la fecha y hora del sistema.
    """

    return {
        "datetime": datetime.now().astimezone().isoformat()
    }
