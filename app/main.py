from app.clients.bitrix import BitrixClient

client = BitrixClient()

print("Checking Bitrix connection")
client.check_connection()